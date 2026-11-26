"""API دفتر فرایندی: طرح درس سالانه، طرح درس روزانه، خلاصه‌ی جلسات، و تأیید/قفل."""
import datetime as dt

from django.core import signing
from django.core.exceptions import PermissionDenied as DjPermissionDenied
from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404
from apps.accounts.models import OrgUnit
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.jalali import format_jalali
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

from . import services as S
from .models import AnnualPlan, AnnualPlanRow, DailyPlan, SessionSummary, Status

TEACHER_ROLES = ("teacher",)
MANAGER_ROLES = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec")
APPROVER_ROLES = ("super_admin", "school_admin", "school_deputy_edu")


def _course_access(user, course, write=False):
    """معلم همان درس کاربرگ را می‌نویسد؛ مدیر سیستم همه‌ی کاربرگ‌ها را می‌بیند، ویرایش و مدیریت می‌کند؛
    مدیر، معاون و سرپرست مدرسه‌ی همان درس را می‌بیند (ویرایش فقط با معلم یا مدیر سیستم)."""
    if user.role == "super_admin":
        return True
    if user.role == "teacher" and course.teacher_id == user.pk:
        return True
    if not write and user.role in MANAGER_ROLES and org_in_scope(user, course.org):
        return True
    if write:
        raise PermissionDenied("این کاربرگ را معلم همان درس تکمیل می‌کند.")
    raise PermissionDenied("دسترسی به این کاربرگ ندارید.")


def _plan_for(course):
    plan, _ = AnnualPlan.objects.get_or_create(course=course)
    return plan


class AnnualPlanView(APIView):
    """GET — طرح سالانه‌ی درس با ردیف‌ها. PUT — ویرایش تعداد جلسات و ردیف‌ها (فقط معلم، تا قبل از تأیید)."""

    def get(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "subject", "classroom__grade", "classroom__study_field", "teacher"), pk=course_id)
        _course_access(request.user, course)
        plan = _plan_for(course)
        total = S.ensure_rows(plan)
        per_week, _ = S.plan_totals(plan)
        return Response({"plan": plan.pk, "status": plan.status, "status_label": plan.get_status_display(),
                         "total_sessions": plan.total_sessions, "sessions_per_week": plan.sessions_per_week,
                         "suggested_total": total, "suggested_per_week": per_week, "note": plan.note,
                         "rows": [{"index": r.index, "date": r.date.isoformat() if r.date else "",
                                   "date_fa": format_jalali(r.date, with_time=False) if r.date else "",
                                   "from_page": r.from_page, "to_page": r.to_page, "topics": r.topics}
                                  for r in plan.rows.all()]})

    def put(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "subject", "classroom__grade", "classroom__study_field"), pk=course_id)
        _course_access(request.user, course, write=True)
        plan = _plan_for(course)
        S.assert_editable(plan)
        d = request.data
        if "total_sessions" in d:
            v = d.get("total_sessions")
            plan.total_sessions = int(v) if v not in (None, "") else None
            if plan.total_sessions is not None and not 1 <= plan.total_sessions <= 200:
                raise serializers.ValidationError({"total_sessions": "تعداد جلسات بین ۱ و ۲۰۰ باشد."})
        if "sessions_per_week" in d:
            v = d.get("sessions_per_week")
            plan.sessions_per_week = int(v) if v not in (None, "") else None
        plan.save()
        S.ensure_rows(plan)
        for row in d.get("rows") or []:
            try:
                r = plan.rows.get(index=int(row["index"]))
            except (KeyError, ValueError, AnnualPlanRow.DoesNotExist):
                continue
            ds = str(row.get("date") or "").strip()
            r.date = dt.date.fromisoformat(ds) if ds else None
            r.from_page = str(row.get("from_page") or "")[:20]
            r.to_page = str(row.get("to_page") or "")[:20]
            r.topics = str(row.get("topics") or "")[:2000]
            r.save()
        audit(request, "classbook.annual.save", f"course:{course.pk}")
        return Response({"ok": True})


class DailyListView(APIView):
    """GET — طرح‌های روزانه‌ی درس. POST — طرح روزانه‌ی تازه (شماره‌ی بعدی خودکار)."""

    def get(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=course_id)
        _course_access(request.user, course)
        return Response([{"id": d.pk, "number": d.number, "date": d.date.isoformat(), "date_fa": format_jalali(d.date, with_time=False),
                          "title": d.title, "status": d.status, "status_label": d.get_status_display()}
                         for d in DailyPlan.objects.filter(course=course)])

    def post(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=course_id)
        _course_access(request.user, course, write=True)
        try:
            day = dt.date.fromisoformat(request.data["date"])
        except (KeyError, ValueError):
            raise serializers.ValidationError({"date": "تاریخ اجرا را درست وارد کنید."})
        nxt = (DailyPlan.objects.filter(course=course).order_by("-number").values_list("number", flat=True).first() or 0) + 1
        d = DailyPlan.objects.create(course=course, number=nxt, date=day, title=str(request.data.get("title") or "")[:200],
                                     duration_minutes=int(request.data.get("duration_minutes") or 45),
                                     place=str(request.data.get("place") or "")[:120], data={})
        audit(request, "classbook.daily.create", f"daily:{d.pk}")
        return Response({"id": d.pk, "number": d.number}, status=201)


class DailyItemView(APIView):
    """GET — یک طرح روزانه با محتوا. PUT — ویرایش (تا قبل از تأیید). DELETE — حذف (تا قبل از تأیید)."""

    def _get(self, pk):
        return get_object_or_404(DailyPlan.objects.select_related("course__org", "course__teacher"), pk=pk)

    def get(self, request, pk):
        d = self._get(pk)
        _course_access(request.user, d.course)
        return Response({"id": d.pk, "number": d.number, "date": d.date.isoformat(), "title": d.title,
                         "duration_minutes": d.duration_minutes, "place": d.place, "data": d.data,
                         "status": d.status, "status_label": d.get_status_display(), "note": d.note})

    def put(self, request, pk):
        d = self._get(pk)
        _course_access(request.user, d.course, write=True)
        S.assert_editable(d)
        if "date" in request.data:
            try:
                d.date = dt.date.fromisoformat(str(request.data["date"]))
            except ValueError:
                raise serializers.ValidationError({"date": "تاریخ اجرا را انتخاب کنید."})
        for f in ("title", "place"):
            if f in request.data:
                setattr(d, f, str(request.data[f] or "")[:200])
        if "duration_minutes" in request.data:
            d.duration_minutes = int(request.data["duration_minutes"])
        if "data" in request.data and isinstance(request.data["data"], dict):
            d.data = request.data["data"]
        d.save()
        return Response({"ok": True})

    def delete(self, request, pk):
        d = self._get(pk)
        _course_access(request.user, d.course, write=True)
        S.assert_editable(d)
        d.delete()
        return Response(status=204)


class SummaryListView(APIView):
    """GET — خلاصه‌ی جلسات درس. POST — ثبت خلاصه‌ی یک جلسه."""

    def get(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=course_id)
        _course_access(request.user, course)
        return Response([{"id": s.pk, "session_no": s.session_no, "date": s.date.isoformat(),
                          "date_fa": format_jalali(s.date, with_time=False), "status": s.status,
                          "status_label": s.get_status_display()} for s in SessionSummary.objects.filter(course=course)])

    def post(self, request, course_id):
        course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=course_id)
        _course_access(request.user, course, write=True)
        try:
            day = dt.date.fromisoformat(request.data["date"])
            no = int(request.data["session_no"])
        except (KeyError, ValueError):
            raise serializers.ValidationError({"session_no": "شماره‌ی جلسه و تاریخ را درست وارد کنید."})
        s, created = SessionSummary.objects.get_or_create(course=course, session_no=no, defaults={"date": day})
        if not created:
            raise serializers.ValidationError({"session_no": "برای این شماره‌ی جلسه خلاصه از قبل ثبت شده است."})
        s.activity = str(request.data.get("activity") or "")[:4000]
        s.homework = str(request.data.get("homework") or "")[:2000]
        s.save()
        return Response({"id": s.pk}, status=201)


class SummaryItemView(APIView):
    def _get(self, pk):
        return get_object_or_404(SessionSummary.objects.select_related("course__org", "course__teacher"), pk=pk)

    def get(self, request, pk):
        s = self._get(pk)
        _course_access(request.user, s.course)
        return Response({"id": s.pk, "session_no": s.session_no, "date": s.date.isoformat(), "activity": s.activity,
                         "homework": s.homework, "status": s.status, "note": s.note})

    def put(self, request, pk):
        s = self._get(pk)
        _course_access(request.user, s.course, write=True)
        S.assert_editable(s)
        if "date" in request.data:
            try:
                s.date = dt.date.fromisoformat(str(request.data["date"]))
            except ValueError:
                raise serializers.ValidationError({"date": "تاریخ جلسه را انتخاب کنید."})
        if "activity" in request.data:
            s.activity = str(request.data["activity"] or "")[:4000]
        if "homework" in request.data:
            s.homework = str(request.data["homework"] or "")[:2000]
        s.save()
        return Response({"ok": True})

    def delete(self, request, pk):
        s = self._get(pk)
        _course_access(request.user, s.course, write=True)
        S.assert_editable(s)
        s.delete()
        return Response(status=204)


KINDS = {"annual": AnnualPlan, "daily": DailyPlan, "summary": SessionSummary}


class WorksheetActionView(APIView):
    """POST /api/classbook/<kind>/<pk>/<action>/ — action: submit (معلم) | approve (مدیر/معاون) | return (مدیر/معاون، با یادداشت)."""

    def post(self, request, kind, pk, action):
        model = KINDS.get(kind)
        if model is None:
            raise serializers.ValidationError({"kind": "نوع کاربرگ ناشناخته است."})
        obj = get_object_or_404(model.objects.select_related("course__org", "course__teacher"), pk=pk)
        course = obj.course
        try:
            if action == "submit":
                _course_access(request.user, course, write=True)
                S.submit(obj)
            elif action in ("approve", "return"):
                if request.user.role not in APPROVER_ROLES or not org_in_scope(request.user, course.org):
                    raise PermissionDenied("تأیید و بازگرداندن کاربرگ با مدیر یا معاون آموزشی است.")
                if action == "approve":
                    S.approve(obj, request.user)
                else:
                    S.return_to_draft(obj, request.data.get("note"))
            else:
                raise serializers.ValidationError({"action": "عملیات ناشناخته است."})
        except ValueError as e:
            return Response({"ok": False, "errors": [str(e)]}, status=422)
        except DjPermissionDenied as e:
            raise PermissionDenied(str(e))
        audit(request, f"classbook.{kind}.{action}", f"{kind}:{obj.pk}")
        return Response({"ok": True, "status": obj.status, "status_label": obj.get_status_display()})


def _disc_access(user, student):
    """کارنامه‌ی انضباطی: خود دانش‌آموز، دبیران درس‌های او، مدیر سیستم، و مدیر/معاون مدرسه‌ی او."""
    from apps.academics.models import ClassRoom
    if user.pk == student.pk or user.role == "super_admin":
        return True
    from apps.courses.models import Enrollment
    if user.role == "teacher":
        from apps.courses.roster import roster_ids
        if Enrollment.objects.filter(student=student, course__teacher=user).exists() or \
                Course.objects.filter(teacher=user, classroom__students=student).exists():
            return True
    if user.role in MANAGER_ROLES:
        cls = ClassRoom.objects.filter(students=student, academic_year__is_current=True).select_related("school").first()
        if cls and org_in_scope(user, cls.school):
            return True
    raise PermissionDenied("کارنامه‌ی انضباطی را فقط خود دانش‌آموز، دبیر او یا مسئول مدرسه می‌بیند.")


class PrintLinkView(APIView):
    """GET ?kind=annual|daily|summary&pk=ID — پیوند کوتاه‌مدت برای چاپ (در تب جدید، با فونت و کادربندی سامانه)."""

    def get(self, request):
        kind = request.query_params.get("kind")
        term = request.query_params.get("term") or "اول"
        if kind in ("disc", "card", "card2"):
            from apps.accounts.models import User
            student = get_object_or_404(User, pk=request.query_params.get("pk") or 0, role="student")
            _disc_access(request.user, student)
            tid = request.user.pk if request.user.role == "teacher" else None
            token = signing.dumps({"k": kind, "p": student.pk, "t": request.query_params.get("term") or "", "tid": tid}, salt="classbook-print")
            audit(request, "classbook.print.disc", f"student:{student.pk}")
            return Response({"url": f"/api/classbook-print/{token}/"})
        if kind in ("g606", "g608"):
            course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=request.query_params.get("pk") or 0)
            _course_access(request.user, course)
            token = signing.dumps({"k": kind, "p": course.pk, "t": term}, salt="classbook-print")
            audit(request, f"classbook.print.{kind}", f"course:{course.pk}")
            return Response({"url": f"/api/classbook-print/{token}/"})
        model = KINDS.get(kind)
        if model is None:
            raise serializers.ValidationError({"kind": "نوع کاربرگ ناشناخته است."})
        obj = get_object_or_404(model.objects.select_related("course__org", "course__teacher"), pk=request.query_params.get("pk") or 0)
        _course_access(request.user, obj.course)
        token = signing.dumps({"k": kind, "p": obj.pk}, salt="classbook-print")
        audit(request, "classbook.print", f"{kind}:{obj.pk}")
        return Response({"url": f"/api/classbook-print/{token}/"})


class PrintPublicView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from .printing import render
        try:
            p = signing.loads(token, salt="classbook-print", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از دفتر فرایندی چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        from .printing import render_606, render_608, render_disc
        if p.get("bulk"):
            from apps.accounts.models import User
            from .card_bulk import render_bulk
            students = list(User.objects.filter(pk__in=p["s"], role="student").order_by("last_name", "first_name", "id"))
            resp = HttpResponse(render_bulk(students, p["bulk"], p.get("z", "a4"), teacher_id=p.get("tid")), content_type="text/html; charset=utf-8")
            resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'self'"
            resp["Cache-Control"] = "no-store"
            return resp
        if p["k"] == "card2":
            from apps.accounts.models import User
            from .gradesheets import TERM_MONTHS
            from .report_card_v2 import build as build2, render_html as render2
            student = get_object_or_404(User, pk=p["p"])
            term = p.get("t") or "اول"
            body = render2(build2(student, TERM_MONTHS.get(term, TERM_MONTHS["اول"]), teacher_id=p.get("tid")), f"کارنامه‌ی نیم‌سال {term}")
        elif p["k"] == "card":
            from apps.accounts.models import User
            from .report_card import build as build_card, render_html as render_card
            student = get_object_or_404(User, pk=p["p"])
            body = render_card(build_card(student, p.get("t") or None, teacher_id=p.get("tid")))
        elif p["k"] == "disc":
            from apps.accounts.models import User
            student = get_object_or_404(User, pk=p["p"])
            body = render_disc(student, p.get("t") or None)
        elif p["k"] in ("g606", "g608"):
            course = get_object_or_404(Course.objects.select_related("org", "teacher", "classroom__grade", "classroom__study_field", "subject"), pk=p["p"])
            fn = render_606 if p["k"] == "g606" else render_608
            body = fn(course, p.get("t") or "اول")
        else:
            model = KINDS[p["k"]]
            obj = get_object_or_404(model.objects.select_related("course__org", "course__teacher", "course__classroom__grade", "course__classroom__study_field", "course__subject"), pk=p["p"])
            body = render(p["k"], obj)
        resp = HttpResponse(body, content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp


class ReportCardXlsxView(APIView):
    """GET ?student=ID&term=اول|دوم&kind=card|card2|disc — کارنامه‌ها به‌صورت Excel (همان داده‌ی چاپی)."""

    def get(self, request):
        from apps.accounts.models import User
        from apps.courses.models import Course  # noqa: F401
        student = get_object_or_404(User, pk=request.query_params.get("student") or 0, role="student")
        _disc_access(request.user, student)
        term = request.query_params.get("term") or "اول"
        kind = request.query_params.get("kind") or "card"
        if kind == "card2":
            from .gradesheets import TERM_MONTHS
            from .report_card_v2 import build as build2, render_xlsx as xlsx2
            data = xlsx2(build2(student, TERM_MONTHS.get(term, TERM_MONTHS["اول"])), f"کارنامه‌ی نیم‌سال {term}")
        elif kind == "disc":
            import io
            from openpyxl import Workbook
            from .discipline_report import build as build_disc
            d = build_disc(student, term)
            wb = Workbook()
            ws = wb.active
            ws.title = "کارنامه‌ی انضباطی"
            ws.sheet_view.rightToLeft = True
            ws.append(["کارنامه‌ی انضباطی", student.get_full_name() or student.username, student.student_code or "", "دوره", term])
            ws.append(["امتیاز پایه", float(d["base"]), "جمع کسرها", float(d["total"]), "امتیاز باقی‌مانده", float(d["remaining"])])
            ws.append([])
            ws.append(["تاریخ", "شرح", "کسر", "مدرسه", "توضیح"])
            for r in d["rows"]:
                ws.append([r["date"], r["title"], float(r["points"]), r["school"], r["note"]])
            buf = io.BytesIO()
            wb.save(buf)
            data = buf.getvalue()
        else:
            from .report_card import build as build_card, render_xlsx
            data = render_xlsx(build_card(student, term if term else None))
        audit(request, "classbook.card.xlsx", f"student:{student.pk} kind:{kind}")
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{kind}-{student.pk}.xlsx"'
        return resp


class ReportStudentsView(APIView):
    """GET ?school=ID&classroom=&grade=&field=&q= — دانش‌آموزان مدرسه با فیلتر کلاس، پایه، رشته و نام/کد.
    همراه با گزینه‌های فیلتر (کلاس‌ها، پایه‌ها، رشته‌ها) از کلاس‌های سال جاری همان مدرسه."""

    def get(self, request):
        from apps.academics.models import ClassRoom
        from apps.accounts.models import User
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        u = request.user
        allowed = u.role == "super_admin" or (u.role in MANAGER_ROLES and org_in_scope(u, school)) \
            or (u.role == "teacher" and Course.objects.filter(org=school, teacher=u).exists())
        if not allowed:
            raise PermissionDenied("فهرست دانش‌آموزان این مدرسه برای شما نیست.")
        classes = ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field")
        if u.role == "teacher":
            classes = classes.filter(pk__in=Course.objects.filter(teacher=u).values("classroom_id"))
        p = request.query_params
        filtered = classes
        if p.get("classroom"):
            filtered = filtered.filter(pk=p["classroom"])
        if p.get("grade"):
            filtered = filtered.filter(grade_id=p["grade"])
        if p.get("field"):
            filtered = filtered.filter(study_field_id=p["field"])
        qs = User.objects.filter(role="student", classrooms__in=filtered).distinct()
        for word in (p.get("q") or "").strip()[:80].split():
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(student_code__icontains=word) | Q(national_id__icontains=word))
        students = list(qs.order_by("last_name", "first_name", "id")[:300])
        cls_name = {}
        for c in filtered.order_by("name"):
            for sid in c.students.values_list("pk", flat=True):
                cls_name.setdefault(sid, f"{c.grade.name} — {c.name}")
        opts_classes = [{"id": c.pk, "label": f"{c.grade.name} — {c.name}", "grade": c.grade_id, "field": c.study_field_id} for c in classes.order_by("grade__name", "name")]
        grades = sorted({(c["grade"], c["label"].split(" — ")[0]) for c in opts_classes}, key=lambda x: x[1])
        fields = sorted({(c.study_field_id, c.study_field.name) for c in classes if c.study_field_id}, key=lambda x: x[1])
        return Response({
            "students": [{"id": x.pk, "name": x.get_full_name() or x.username, "code": x.student_code or "",
                          "national": x.national_id or "", "class": cls_name.get(x.pk, "")} for x in students],
            "classes": opts_classes,
            "grades": [{"id": g, "label": l} for g, l in grades],
            "fields": [{"id": f, "label": l} for f, l in fields],
        })


class CardBulkLinkView(APIView):
    """GET ?type=half1|half2|card|disc&size=a4|a5&students=ID,ID — پیوند چاپ گروهی کارنامه‌ها (هر کارنامه یک صفحه)."""

    def get(self, request):
        from apps.accounts.models import User
        from .card_bulk import TYPES
        type_key = request.query_params.get("type") or "half1"
        if type_key not in TYPES:
            raise serializers.ValidationError({"type": "نوع کارنامه ناشناخته است."})
        ids = [int(x) for x in (request.query_params.get("students") or "").split(",") if x.isdigit()]
        students = list(User.objects.filter(pk__in=ids, role="student").order_by("last_name", "first_name", "id"))
        for st in students:
            _disc_access(request.user, st) if type_key == "disc" else None
        if not students:
            raise serializers.ValidationError({"students": "حداقل یک دانش‌آموز را انتخاب کنید."})
        size = request.query_params.get("size") if request.query_params.get("size") in ("a4", "a5") else "a4"
        tid = request.user.pk if request.user.role == "teacher" else None
        token = signing.dumps({"bulk": type_key, "z": size, "s": [s.pk for s in students], "tid": tid}, salt="classbook-print")
        audit(request, "classbook.print.card_bulk", f"type:{type_key} n:{len(students)} size:{size}")
        return Response({"url": f"/api/classbook-print/{token}/", "count": len(students)})


class CardNoteView(APIView):
    """GET ?students=ID,…&term=اول|دوم — توضیحات کارنامه‌ها.
    POST {students:[…], term, text} — مدیر و معاون آموزشی توضیح کارنامه‌ی دانش‌آموزان انتخاب‌شده را می‌نویسد."""

    def get(self, request):
        from apps.classbook.models import ReportCardNote
        ids = [int(x) for x in (request.query_params.get("students") or "").split(",") if x.isdigit()]
        term = request.query_params.get("term") or "اول"
        notes = {n.student_id: n.text for n in ReportCardNote.objects.filter(student_id__in=ids, term=term)}
        return Response({str(i): notes.get(i, "") for i in ids})

    def post(self, request):
        from apps.classbook.models import ReportCardNote
        from apps.accounts.models import User as U
        if request.user.role not in ("super_admin", "school_admin", "school_deputy_edu"):
            raise PermissionDenied("توضیح کارنامه را مدیر یا معاون آموزشی می‌نویسد.")
        term = request.data.get("term") or "اول"
        if term not in ("اول", "دوم", "سالانه", "انضباطی"):
            raise serializers.ValidationError({"term": "نوع کارنامه‌ی توضیح نامعتبر است."})
        text = str(request.data.get("text") or "")[:3000]
        ids = [int(x) for x in (request.data.get("students") or []) if str(x).isdigit()]
        for st in U.objects.filter(pk__in=ids, role="student"):
            ReportCardNote.objects.update_or_create(student=st, term=term, defaults={"text": text, "updated_by": request.user})
        audit(request, "classbook.card_note", f"term:{term} n:{len(ids)}")
        return Response({"ok": True, "saved": len(ids)})



class ReportOptionsView(APIView):
    """GET ?school=ID — گزینه‌های پایه، رشته و کلاس‌های سال جاری مدرسه (برای کادرهای کارنامه‌ها)."""

    def get(self, request):
        from apps.academics.models import ClassRoom
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        u = request.user
        teaches_here = u.role == "teacher" and Course.objects.filter(org=school, teacher=u).exists()
        if not (u.role == "super_admin" or (u.role in MANAGER_ROLES and org_in_scope(u, school)) or teaches_here):
            raise PermissionDenied("گزینه‌های کارنامه‌ها برای شما نیست.")
        classes_qs = ClassRoom.objects.filter(school=school, academic_year__is_current=True)
        if u.role == "teacher":
            classes_qs = classes_qs.filter(pk__in=Course.objects.filter(teacher=u).values("classroom_id"))
        classes = list(classes_qs.select_related("grade", "study_field").order_by("grade__name", "name"))
        grades, fields, seen_g, seen_f = [], [], set(), set()
        for c in classes:
            if c.grade_id and c.grade_id not in seen_g:
                seen_g.add(c.grade_id); grades.append({"id": c.grade_id, "label": c.grade.name})
            if c.study_field_id and c.study_field_id not in seen_f:
                seen_f.add(c.study_field_id); fields.append({"id": c.study_field_id, "label": c.study_field.name})
        return Response({
            "classes": [{"id": c.pk, "label": f"{c.grade.name} — {c.name}", "grade": c.grade_id, "field": c.study_field_id} for c in classes],
            "grades": grades, "fields": fields,
            "empty": not classes,
        })
