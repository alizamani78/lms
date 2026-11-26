from datetime import date as _date
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.attendance.models import AttendanceRecord
from apps.attendance.views import summarize
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.evaluation.logic import course_percentages

from .models import FIELD_TYPES, ProcessDefinition, ProcessEntry, TeacherEvaluation

DEPUTY = "school_deputy_edu"
ENTRY_WRITERS = ("teacher", "school_deputy_edu", "school_admin")         # the teacher and the educational deputy fill the book
SENSITIVE_READERS = ("school_deputy_edu", "school_admin")                 # health data: the deputy and the principal


def _school(sid, must=True):
    if not str(sid or "").isdigit():
        raise serializers.ValidationError({"school": "مدرسه را انتخاب کنید."})
    return get_object_or_404(OrgUnit, pk=int(sid), kind="school")


def _clean_fields(raw):
    if not isinstance(raw, list) or len(raw) > 30:
        raise serializers.ValidationError({"fields": "فهرست پرسش‌ها نامعتبر است (حداکثر ۳۰)."})
    out, keys = [], set()
    for i, f in enumerate(raw):
        label = str(f.get("label", "")).strip()[:120]
        typ = f.get("type") if f.get("type") in FIELD_TYPES else "text"
        if not label:
            raise serializers.ValidationError({"fields": f"پرسش {i + 1} عنوان ندارد."})
        choices = [c.strip()[:80] for c in str(f.get("choices", "")).split("،") if c.strip()] if typ == "choice" else []
        if typ == "choice" and not choices:
            raise serializers.ValidationError({"fields": f"برای پرسش «{label}» گزینه‌ها را با ، بنویسید."})
        key = f"f{i + 1}"
        keys.add(key)
        out.append({"key": key, "label": label, "type": typ, "choices": choices, "required": bool(f.get("required", False))})
    return out


def _definition_dict(d):
    return {"id": d.pk, "title": d.title, "description": d.description, "sensitive": d.sensitive, "fields": d.fields, "is_active": d.is_active}


class DefinitionsView(APIView):
    """GET ?school= — the processes of the school. POST — the educational deputy defines a new process."""

    def get(self, request):
        u = request.user
        school = _school(request.query_params.get("school"))
        if not org_in_scope(u, school):
            raise PermissionDenied()
        rows = ProcessDefinition.objects.filter(school=school).order_by("title")
        if u.role not in SENSITIVE_READERS and u.role != "super_admin":
            rows = rows.exclude(sensitive=True)
        return Response([_definition_dict(d) for d in rows])

    def post(self, request):
        u = request.user
        school = _school(request.data.get("school"))
        if u.role != DEPUTY or not org_in_scope(u, school):
            raise PermissionDenied("فرایندها را فقط معاون آموزشی مدرسه تعریف می‌کند.")
        title = str(request.data.get("title", "")).strip()[:200]
        if not title:
            raise serializers.ValidationError({"title": "عنوان فرایند لازم است."})
        d = ProcessDefinition.objects.create(school=school, title=title, description=str(request.data.get("description", ""))[:2000],
                                             sensitive=bool(request.data.get("sensitive")), fields=_clean_fields(request.data.get("fields") or []),
                                             created_by=u)
        audit(request, "process.define", f"process:{d.pk}")
        return Response(_definition_dict(d), status=201)


class DefinitionDetailView(APIView):
    def patch(self, request, pk):
        d = get_object_or_404(ProcessDefinition, pk=pk)
        if request.user.role != DEPUTY or not org_in_scope(request.user, d.school):
            raise PermissionDenied()
        if "title" in request.data and str(request.data["title"]).strip():
            d.title = str(request.data["title"]).strip()[:200]
        if "fields" in request.data:
            d.fields = _clean_fields(request.data["fields"])
        if "sensitive" in request.data:
            d.sensitive = bool(request.data["sensitive"])
        if "is_active" in request.data:
            d.is_active = bool(request.data["is_active"])
        d.save()
        audit(request, "process.update", f"process:{d.pk}")
        return Response(_definition_dict(d))


class EntriesView(APIView):
    """POST — the teacher or the educational deputy records one entry of a process for a student."""

    def post(self, request):
        u = request.user
        d = get_object_or_404(ProcessDefinition, pk=request.data.get("definition") or 0, is_active=True)
        if u.role not in ENTRY_WRITERS or not org_in_scope(u, d.school):
            raise PermissionDenied()
        student = get_object_or_404(User, pk=request.data.get("student") or 0, role="student")
        if student.org_id != d.school_id:
            raise serializers.ValidationError({"student": "دانش‌آموز این مدرسه نیست."})
        values = request.data.get("values") or {}
        clean = {}
        for f in d.fields:
            raw = values.get(f["key"])
            if raw in (None, ""):
                if f.get("required"):
                    raise serializers.ValidationError({f["key"]: f"«{f['label']}» الزامی است."})
                continue
            if f["type"] == "number":
                try:
                    clean[f["key"]] = float(Decimal(str(raw)))
                except Exception:
                    raise serializers.ValidationError({f["key"]: f"«{f['label']}» باید عدد باشد."})
            elif f["type"] == "date":
                try:
                    clean[f["key"]] = _date.fromisoformat(str(raw)[:10]).isoformat()
                except ValueError:
                    raise serializers.ValidationError({f["key"]: f"«{f['label']}» تاریخ نامعتبر است."})
            elif f["type"] == "bool":
                clean[f["key"]] = bool(raw)
            elif f["type"] == "choice":
                if str(raw) not in f["choices"]:
                    raise serializers.ValidationError({f["key"]: f"«{f['label']}» یکی از گزینه‌ها نیست."})
                clean[f["key"]] = str(raw)
            else:
                clean[f["key"]] = str(raw)[:2000]
        e = ProcessEntry.objects.create(definition=d, school=d.school, student=student, values=clean,
                                        date=str(request.data.get("date") or _date.today().isoformat())[:10],
                                        note=str(request.data.get("note", ""))[:500], recorded_by=u)
        audit(request, "process.entry", f"entry:{e.pk} student:{student.pk}")
        return Response({"id": e.pk}, status=201)


class StudentBookView(APIView):
    """GET /api/processbook/student/<id>/ — the student's process book: every process with its entries, the attendance
    summary, the academic summary (average and weak courses) and the discipline score. Sensitive processes only for the
    deputy and the principal (and the teacher who recorded them)."""

    def get(self, request, pk):
        u = request.user
        student = get_object_or_404(User, pk=pk, role="student")
        if u.role not in ENTRY_WRITERS + ("school_deputy_exec", "school_deputy_culture") or not org_in_scope(u, student.org):
            raise PermissionDenied()
        school = student.org
        defs = ProcessDefinition.objects.filter(school=school, is_active=True).order_by("title")
        entries = ProcessEntry.objects.filter(student=student).select_related("recorded_by", "definition")
        out = []
        for d in defs:
            if d.sensitive and u.role not in SENSITIVE_READERS:
                continue
            rows = [{"id": e.pk, "date": e.date, "values": e.values, "note": e.note,
                     "recorded_by": (e.recorded_by.get_full_name() or e.recorded_by.username) if e.recorded_by_id else ""}
                    for e in entries if e.definition_id == d.pk]
            out.append({"definition": _definition_dict(d), "entries": rows})
        att = summarize(AttendanceRecord.objects.filter(student=student))
        pct = course_percentages([school.pk], student_ids=[student.pk])
        marks = []
        for (sid, cid), rec in pct.items():
            items = rec[2] + rec[3]
            if items:
                marks.append({"course": rec[0], "percent": round(sum(items) / len(items), 1)})
        avg = round(sum(m["percent"] for m in marks) / len(marks), 1) if marks else None
        weak = [m["course"] for m in marks if m["percent"] < 50]
        from apps.discipline.views import worksheet as discipline_sheet
        disc = discipline_sheet(student)
        return Response({"student": {"id": student.pk, "name": student.get_full_name() or student.username, "school": school.name},
                         "attendance": att, "academic": {"average": avg, "courses": marks, "weak_courses": weak},
                         "discipline": {"score": disc["score"], "total_points": disc["total_points"]},
                         "processes": out})


class EvaluationsView(APIView):
    """GET ?school=&teacher= — teacher evaluations. POST — the educational deputy evaluates a teacher (1 to 5 per criterion)."""

    def get(self, request):
        u = request.user
        school = _school(request.query_params.get("school"))
        if u.role not in (DEPUTY, "school_admin") or not org_in_scope(u, school):
            if not (u.role == "teacher" and str(u.pk) == str(request.query_params.get("teacher"))):
                raise PermissionDenied()
        qs = TeacherEvaluation.objects.filter(school=school).select_related("teacher")
        if request.query_params.get("teacher", "").isdigit():
            qs = qs.filter(teacher_id=int(request.query_params["teacher"]))
        return Response([{"id": e.pk, "teacher": e.teacher.get_full_name() or e.teacher.username, "date": e.date, "scores": e.scores,
                          "average": float(e.average), "comment": e.comment} for e in qs[:300]])

    def post(self, request):
        u = request.user
        school = _school(request.data.get("school"))
        if u.role != DEPUTY or not org_in_scope(u, school):
            raise PermissionDenied("ارزشیابی معلم را معاون آموزشی انجام می‌دهد.")
        teacher = get_object_or_404(User, pk=request.data.get("teacher") or 0, role="teacher")
        if not org_in_scope(u, teacher.org) and not teacher.school_memberships.filter(school=school).exists():
            raise serializers.ValidationError({"teacher": "این معلم در این مدرسه نیست."})
        crit = dict(TeacherEvaluation.CRITERIA)
        scores = {}
        for key in crit:
            try:
                v = int(request.data.get("scores", {}).get(key))
            except (TypeError, ValueError):
                raise serializers.ValidationError({key: f"امتیاز «{crit[key]}» (۱ تا ۵) لازم است."})
            if not 1 <= v <= 5:
                raise serializers.ValidationError({key: f"امتیاز «{crit[key]}» باید بین ۱ تا ۵ باشد."})
            scores[key] = v
        avg = Decimal(sum(scores.values())) / Decimal(len(scores))
        e = TeacherEvaluation.objects.create(school=school, teacher=teacher, evaluator=u, scores=scores,
                                             average=avg.quantize(Decimal("0.01")), comment=str(request.data.get("comment", ""))[:3000],
                                             date=str(request.data.get("date") or _date.today().isoformat())[:10])
        audit(request, "process.evaluation", f"evaluation:{e.pk} teacher:{teacher.pk}")
        return Response({"id": e.pk, "average": float(e.average)}, status=201)
