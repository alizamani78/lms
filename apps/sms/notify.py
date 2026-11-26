"""Messages to parents about attendance, exams and assignments. Written when the event happens (signals) or when a
deadline passes (the periodic scan); sent by the outbox flush through the school's own SMS panel."""
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import ParentLink, User

from .models import SmsGateway, SmsOutbox
from .providers import SmsError, send
from .recovery import normalize_mobile

MAX_ATTEMPTS = 5


def parents_of(student):
    links = ParentLink.objects.filter(student=student, is_verified=True, notify_sms=True).select_related("parent")
    out = []
    for link in links:
        mob = normalize_mobile(link.parent.mobile)
        if mob:
            out.append((link.parent, mob))
    return out


def enqueue(student, school, purpose, text, key):
    """Queue the same message for every verified parent with a mobile. Repeats of the same key are ignored."""
    if school is None:
        return 0
    made = 0
    for parent, mobile in parents_of(student):
        _, created = SmsOutbox.objects.get_or_create(
            dedupe_key=f"{key}:{parent.pk}",
            defaults={"school": school, "parent": parent, "student": student, "purpose": purpose,
                      "mobile": mobile, "text": text[:500]})
        made += int(created)
    return made


def enqueue_user(person, school, purpose, text, key):
    """Queue one message to a person (a meeting invitation, for example). Repeats of the same key are ignored."""
    from apps.accounts.models import User as _User
    mob = normalize_mobile(person.mobile)
    if not mob or school is None:
        return 0
    _, created = SmsOutbox.objects.get_or_create(
        dedupe_key=f"{key}:{person.pk}",
        defaults={"school": school, "parent": person, "student": None, "purpose": purpose, "mobile": mob, "text": text[:500]})
    return int(created)


def _name(u):
    return u.get_full_name() or u.username


def flush(limit=200):
    """Send queued messages through each school's active panel. Returns how many were sent."""
    sent = 0
    rows = SmsOutbox.objects.filter(status="queued", attempts__lt=MAX_ATTEMPTS).select_related("school")[:limit]
    gateways = {}
    for row in rows:
        gw = gateways.get(row.school_id)
        if gw is None and row.school_id not in gateways:
            gw = SmsGateway.objects.filter(school_id=row.school_id, is_active=True).first()
            gateways[row.school_id] = gw
        if gw is None:
            row.status = "skipped"; row.last_error = "پنل پیامکی فعال نیست"; row.save(update_fields=["status", "last_error"])
            continue
        row.attempts += 1
        try:
            ok, detail = send(gw, row.mobile, row.text)
        except SmsError as e:
            ok, detail = False, str(e)[:100]
        row.gateway = gw
        if ok:
            row.status, row.sent_at, row.last_error = "sent", timezone.now(), ""
            sent += 1
        else:
            row.last_error = detail[:120]
            row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "queued"
        row.save()
    return sent


def scan_deadlines(window_days=2):
    """Assignments whose due time passed and exams that ended: tell the parents who did / did not take part."""
    from apps.coursework.models import Assignment, Submission
    from apps.courses.models import Enrollment
    from apps.exams.models import Attempt, Exam
    now = timezone.now()
    since = now - timedelta(days=window_days)
    for a in Assignment.objects.filter(due_at__lte=now, due_at__gte=since, is_published=True).select_related("course__org"):
        done = set(Submission.objects.filter(assignment=a).values_list("student_id", flat=True))
        for e in Enrollment.objects.filter(course=a.course).select_related("student"):
            if e.student_id in done:
                continue
            text = f"{_name(e.student)} عزیز، تکلیف «{a.title}» درس {a.course.title} انجام نشده است. — {a.course.org.name}"
            enqueue(e.student, a.course.org, "homework_missing", text, f"hw-miss:{a.pk}:{e.student_id}")
    for ex in Exam.objects.filter(ends_at__lte=now, ends_at__gte=since, is_published=True).select_related("course__org"):
        attempts = {a_.student_id: a_ for a_ in Attempt.objects.filter(exam=ex)}
        for e in Enrollment.objects.filter(course=ex.course).select_related("student"):
            att = attempts.get(e.student_id)
            if att is not None and att.submitted_at:
                text = f"{_name(e.student)} عزیز، در آزمون «{ex.title}» درس {ex.course.title} شرکت کرد و آزمون را کامل ثبت کرد. — {ex.course.org.name}"
                enqueue(e.student, ex.course.org, "exam_taken", text, f"exam-taken:{ex.pk}:{e.student_id}")
            elif att is not None:
                text = f"{_name(e.student)} عزیز، در آزمون «{ex.title}» درس {ex.course.title} شرکت کرد ولی آزمون به‌طور کامل ثبت نشد (قطع اتصال). — {ex.course.org.name}"
                enqueue(e.student, ex.course.org, "exam_interrupted", text, f"exam-int:{ex.pk}:{e.student_id}")
            else:
                text = f"{_name(e.student)} عزیز، در آزمون «{ex.title}» درس {ex.course.title} شرکت نکرد. — {ex.course.org.name}"
                enqueue(e.student, ex.course.org, "exam_absent", text, f"exam-absent:{ex.pk}:{e.student_id}")
