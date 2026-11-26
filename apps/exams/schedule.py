"""برنامه‌ی امتحانی مدرسه: ساخت خودکار (با توجه به سختی درس، تعطیلات رسمی تقویم و فاصله‌ی بین امتحان‌ها)،
تأیید و ویرایش دستی با کنترل قواعد. قواعد:
  - هیچ امتحانی در تعطیل رسمی یا جمعه گذاشته نمی‌شود.
  - هر کلاس در هر روز حداکثر یک امتحان دارد.
  - هر امتحان سخت، یک روز فاصله (استراحت) تا امتحان بعدی همان کلاس دارد؛ درس‌های سخت‌تر زودتر برنامه می‌شوند.
  - تعداد امتحان در هر روز مدرسه محدود است (پیش‌فرض ۶)، تا روز شلوغ نشود.
"""
import datetime as dt
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from apps.academics.models import ClassRoom, Subject
from apps.courses.models import Course
from apps.schoolcal.models import Occasion

DEFAULT_OFF = (4,)  # جمعه (شماره‌ی روز در پایتون)


def parse_off(text):
    """«3,4» → (3, 4). جمعه همیشه تعطیل است."""
    days = {4}
    for x in str(text or "").split(","):
        if x.strip().isdigit() and 0 <= int(x) <= 6:
            days.add(int(x))
    return tuple(sorted(days))


def holiday_dates(start, end):
    return set(Occasion.objects.filter(gregorian_date__range=(start, end), is_holiday=True, is_active=True)
               .values_list("gregorian_date", flat=True))


def is_school_day(day, holidays, off=DEFAULT_OFF):
    return day.weekday() not in off and day not in holidays


def school_days(start, end, holidays, off=DEFAULT_OFF):
    d = start
    while d <= end:
        if is_school_day(d, holidays, off):
            yield d
        d += dt.timedelta(days=1)


DEFAULT_REST = {1: 0, 2: 0, 3: 1, 4: 2, 5: 3}   # روز استراحت قبل از امتحان هر درس، بر اساس سختی آن


def plan(school, start, end, start_time, duration, per_day_limit=6, classes=None, off=DEFAULT_OFF, shifts=None,
         rest_map=None, grade_shift=None):
    """پیشنهاد برنامه: فهرست ردیف‌ها (بدون ذخیره) و فهرست علت‌ها.

    قواعد:
      - تعطیلات رسمی و روزهای تعطیل هفته (جمعه، و پنجشنبه اگر انتخاب نشده) امتحان ندارند.
      - دروس سخت‌تر زودتر قرار می‌گیرند و برای هر درس، روزهای استراحت قبل از امتحان (بر اساس سختی) رعایت می‌شود.
      - هر کلاس در هر روز فقط یک امتحان دارد؛ هر پایه در یک نوبت هم‌زمان فقط یک امتحان دارد.
      - نوبت هر پایه: اگر در grade_shift تعیین شده باشد همان؛ وگرنه نوبت‌های عمومی به ترتیب.
    """
    holidays = holiday_dates(start, end)
    days = list(school_days(start, end, holidays, off))
    from apps.academics.models import SchoolSubjectSetting
    diffs = dict(SchoolSubjectSetting.objects.filter(school=school).values_list("subject_id", "difficulty"))
    if not days:
        return [], ["در این بازه روز مدرسه‌ی بدون تعطیل وجود ندارد."]
    rest = dict(DEFAULT_REST)
    rest.update({int(k): int(v) for k, v in (rest_map or {}).items()})
    qs = ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade")
    if classes:
        qs = qs.filter(pk__in=classes)
    if not qs.exists():
        return [], ["در سال تحصیلی جاری کلاسی برای این مدرسه ثبت نشده است؛ ابتدا کلاس‌ها را در «مدارس و کلاس‌ها» وارد کنید."]
    general = list(shifts or [(start_time, duration)])
    per_day = defaultdict(int)
    used = defaultdict(set)          # (روز، پایه) ← نوبت‌های اشغال‌شده
    rows, errors = [], []
    for cls in qs.order_by("grade__order", "name"):
        courses = Course.objects.filter(classroom=cls, subject__isnull=False).select_related("subject").distinct()
        subjects = sorted({c.subject for c in courses}, key=lambda x: (-x.difficulty, x.name))
        if not subjects:
            errors.append(f"برای کلاس «{cls.name}» درسی ثبت نشده است؛ درس‌های کلاس را در «افزودن درس‌های کلاس» تعریف کنید.")
            continue
        cls_shifts = [grade_shift[cls.grade_id]] if grade_shift and cls.grade_id in grade_shift else general
        last_day, taken = None, set()
        for sub in subjects:
            need = rest.get(diffs.get(sub.pk, sub.difficulty), 0)
            placed = False
            for d in days:
                if per_day[d] >= per_day_limit or d in taken:
                    continue
                if last_day is not None and (d - last_day).days <= need:
                    continue
                sh = next((x for x in cls_shifts if x not in used[(d, cls.grade_id)]), None)
                if sh is None:
                    continue
                rows.append({"classroom": cls.pk, "subject": sub.pk, "date": d, "start_time": sh[0],
                             "duration_minutes": sh[1], "difficulty": diffs.get(sub.pk, sub.difficulty),
                             "grade": cls.grade_id, "shift": sh})
                used[(d, cls.grade_id)].add(sh)
                per_day[d] += 1
                taken.add(d)
                last_day = d
                placed = True
                break
            if not placed:
                errors.append(f"برای درس «{sub.name}» در کلاس «{cls.name}» روز مناسب در این بازه پیدا نشد "
                              f"(استراحت لازم قبل از امتحان: {need} روز). بازه را بزرگ‌تر کنید یا نوبت یا استراحت را کم کنید.")
    rows = align_grades(rows, days, list(shifts or [(start_time, duration)]), grade_shift=grade_shift)
    return rows, errors


def check_entry(schedule, classroom_id, subject_id, date, exclude_pk=None):
    """کنترل یک امتحان (ویرایش دستی): تعطیل، روز تعطیل هفته، یک امتحان در روز برای کلاس، و درس تکراری."""
    from .models import ExamScheduleEntry
    errs = []
    holidays = holiday_dates(date, date)
    if not is_school_day(date, holidays, parse_off(schedule.off_days)):
        errs.append("این روز تعطیل رسمی یا تعطیل هفته‌ی مدرسه است.")
    others = ExamScheduleEntry.objects.filter(schedule=schedule, classroom_id=classroom_id).exclude(pk=exclude_pk or 0)
    if others.filter(date=date).exists():
        errs.append("این کلاس امروز امتحان دیگری دارد (هر کلاس در یک روز یک امتحان).")
    if ExamScheduleEntry.objects.filter(schedule=schedule, classroom_id=classroom_id, subject_id=subject_id).exclude(pk=exclude_pk or 0).exists():
        errs.append("این درس برای این کلاس قبلاً در برنامه است.")
    return errs


@transaction.atomic
def save_plan(schedule, rows):
    from .models import ExamScheduleEntry
    ExamScheduleEntry.objects.filter(schedule=schedule).delete()
    ExamScheduleEntry.objects.bulk_create([ExamScheduleEntry(
        schedule=schedule, classroom_id=r["classroom"], subject_id=r["subject"], date=r["date"],
        start_time=r["start_time"], duration_minutes=r["duration_minutes"], venue_name=r.get("venue_name", ""),
        venue_address=r.get("venue_address", "")) for r in rows])
    sync_exams(schedule)


def sync_exams(schedule):
    """هر ردیف برنامه (پایه/کلاس، درس، روز، ساعت شروع، مدت) یک آزمون با همان زمان می‌شود.
    پایه‌ی دهم ساعت ۸ و پایه‌ی یازدهم ساعت ۱۰ در یک روز: هر کدام زمان خودشان را دارند.
    آزمون‌ها منتشر نمی‌شوند؛ انتشار را مدیر یا معاون از همان برنامه انجام می‌دهد."""
    import datetime as dt
    from django.utils import timezone as tz
    from apps.courses.models import Course
    from .models import Exam, ExamScheduleEntry
    missing = []
    for entry in ExamScheduleEntry.objects.filter(schedule=schedule).select_related("classroom__grade", "subject"):
        course = Course.objects.filter(classroom_id=entry.classroom_id, subject_id=entry.subject_id).first()
        if course is None:
            missing.append(f"{entry.classroom.grade.name} — {entry.classroom.name} / {entry.subject.name}")
            continue
        start = tz.make_aware(dt.datetime.combine(entry.date, entry.start_time))
        end = start + dt.timedelta(minutes=entry.duration_minutes)
        title = f"{entry.subject.name} — {entry.classroom.grade.name} {entry.classroom.name}"[:200]
        exam = entry.exam or Exam(course=course)
        exam.course = course
        exam.title = title
        exam.starts_at = start
        exam.ends_at = end
        exam.duration_minutes = entry.duration_minutes
        exam.save()
        if entry.exam_id != exam.pk:
            entry.exam = exam
            entry.save(update_fields=["exam"])
    return missing


def approve(schedule, user):
    from .models import ExamSchedule
    ExamSchedule.objects.filter(school=schedule.school, status=ExamSchedule.APPROVED).update(status=ExamSchedule.ARCHIVED)
    schedule.status = ExamSchedule.APPROVED
    schedule.approved_by = user
    schedule.approved_at = timezone.now()
    schedule.save(update_fields=["status", "approved_by", "approved_at"])


def align_grades(rows, days, shifts, grade_shift=None):
    """همه‌ی کلاس‌های یک پایه برای یک درس، در یک روز و در یک نوبت قرار می‌گیرند (اگر امکانش باشد).
    اگر برای یک گروه جای مشترک پیدا نشود، ردیف‌های آن همان جای قبلی را نگه می‌دارند."""
    groups = {}
    for r in rows:
        groups.setdefault((r.get("grade"), r["subject"]), []).append(r)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        classes = {m["classroom"] for m in members}
        placed = None
        # a grade with its own hours keeps them; the others use the general shifts
        options = [grade_shift[key[0]]] if grade_shift and key[0] in grade_shift else shifts
        for d in sorted(days):
            for sh in options:
                ok = True
                for r in rows:
                    if r in members:
                        continue
                    if r["date"] == d and (r["classroom"] in classes or (r.get("grade") == key[0] and r["shift"] == sh)):
                        ok = False
                        break
                if not ok:
                    continue
                if any(m["date"] == d and m["classroom"] in classes and m is not None for m in members if False):
                    continue
                placed = (d, sh)
                break
            if placed:
                break
        if placed:
            for m in members:
                m["date"], m["shift"] = placed
                m["start_time"], m["duration_minutes"] = placed[1]
    return rows

