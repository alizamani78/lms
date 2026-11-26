"""تخصیص دانش‌آموز به کلاس: (۱) انتخاب چندتایی از فهرست پایه و رشته‌ی کلاس؛ (۲) الصاق فهرست سیدا (کد، نام، نام خانوادگی، پایه).

کد دانش‌آموز: ارقام کد سیدا اگر کمتر از ۱۰ رقم باشد، از سمت چپ با صفر تکمیل می‌شود، سپس با کد سامانه مطابقت داده می‌شود.
دانش‌آموز باید قبلاً در سامانه ثبت شده باشد؛ پیش از افزودن، تطابق نشان داده می‌شود و فقط با تأیید اضافه می‌شود.
"""
import re

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import User
from apps.core.models import audit
from apps.core.scope import org_in_scope

ROLES = ("super_admin", "school_admin", "school_deputy_edu")


def pad_code(code):
    c = re.sub(r"\D", "", str(code or ""))
    return c.zfill(10) if c and len(c) < 10 else c


def _check(user, cls):
    if user.role not in ROLES or not org_in_scope(user, cls.school):
        raise PermissionDenied("تخصیص دانش‌آموز به کلاس را مدیر یا معاون آموزشی مدرسه انجام می‌دهد.")


def _name(u):
    return (f"{u.first_name} {u.last_name}").strip() or u.username


class EligibleStudentsView(APIView):
    """GET ?q= — دانش‌آموزان پایه و رشته‌ی این کلاس که هنوز در آن نیستند (و دانش‌آموزان بدون پایه/رشته)."""

    def get(self, request, pk):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "grade", "study_field"), pk=pk)
        _check(request.user, cls)
        qs = User.objects.filter(role="student", org=cls.school).exclude(classrooms=cls)
        qs = qs.filter((Q(grade=cls.grade) & (Q(study_field=cls.study_field) | Q(study_field__isnull=True))) | Q(grade__isnull=True))
        q = str(request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(student_code__icontains=q) | Q(national_id__icontains=q))
        out = []
        for u in qs.order_by("last_name", "first_name", "id")[:300]:
            other = u.classrooms.filter(academic_year__is_current=True).values_list("name", flat=True).first()
            out.append({"id": u.pk, "name": _name(u), "code": u.student_code or "", "grade": u.grade.name if u.grade_id else "",
                        "in_class": other or ""})
        return Response(out)


class AssignStudentsView(APIView):
    """POST {students:[id,...]} — افزودن دانش‌آموزان انتخاب‌شده به کلاس (پایه و رشته‌ی آن‌ها هم تنظیم می‌شود)."""

    def post(self, request, pk):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "grade", "study_field"), pk=pk)
        _check(request.user, cls)
        ids = [int(x) for x in (request.data.get("students") or []) if str(x).isdigit()]
        if not ids:
            raise serializers.ValidationError({"students": "حداقل یک دانش‌آموز را انتخاب کنید."})
        students = list(User.objects.filter(pk__in=ids, role="student", org=cls.school))
        clashes = []
        for u in students:
            other = other_class_of(u, cls)
            if other is not None:
                clashes.append(f"{u.get_full_name() or u.username} قبلاً در کلاس «{other.name}» ثبت شده است.")
        if clashes:
            raise serializers.ValidationError({"students": clashes})
        for u in students:
            cls.students.add(u)
            u.grade = cls.grade
            u.study_field = cls.study_field
            u.save(update_fields=["grade", "study_field"])
        audit(request, "class.assign.students", f"class:{cls.pk} count:{len(students)}")
        return Response({"added": len(students)}, status=201)


class PasteAssignView(APIView):
    """POST {text, commit} — فهرست سیدا (کد، نام، نام خانوادگی، پایه). بدون commit فقط تطابق را نشان می‌دهد."""

    def post(self, request, pk):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "grade", "study_field"), pk=pk)
        _check(request.user, cls)
        text = str(request.data.get("text") or "")
        commit = bool(request.data.get("commit"))
        found, missing, bad = [], [], []
        seen = set()
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            m = re.search(r"\d{6,10}", line)
            if not m:
                bad.append(f"سطر {n}: کد دانش‌آموزی پیدا نشد.")
                continue
            code = pad_code(m.group(0))
            if code in seen:
                continue
            seen.add(code)
            parts = [p for p in re.split(r"\t|\s{2,}", line.strip()) if p and not p.isdigit()]
            # the name and the family name come apart: the first token is the name, the rest is the family name
            # (a grade word at the end of the line is not part of the family name)
            words = [w for w in " ".join(parts).split() if not w.isdigit()]
            grade_words = {"اول", "دوم", "سوم", "هفتم", "هشتم", "نهم", "دهم", "یازدهم", "دوازدهم", "اول‌متوسطه", "دوم‌متوسطه"}
            if len(words) > 1 and words[-1] in grade_words:
                words = words[:-1]
            first_name = words[0] if words else ""
            family_name = " ".join(words[1:]) if len(words) > 1 else ""
            u = User.objects.filter(role="student", org=cls.school).filter(Q(student_code=code) | Q(national_id=code)).first()
            if u is None:
                missing.append({"line": n, "code": code, "name": first_name[:80], "family": family_name[:80]})
                continue
            found.append({"id": u.pk, "code": code, "name": (u.first_name or first_name)[:80], "family": (u.last_name or family_name)[:80], "file_name": " ".join(parts)[:80]})
        if commit:
            blocked = []
            for row in found:
                u = User.objects.get(pk=row["id"])
                other = other_class_of(u, cls)
                if other is not None:
                    blocked.append(f"{row['code']} ({row.get('name', '')}) قبلاً در کلاس «{other.name}» ثبت شده و اضافه نشد.")
                    continue
                cls.students.add(u)
                u.grade = cls.grade
                u.study_field = cls.study_field
                u.save(update_fields=["grade", "study_field"])
            audit(request, "class.assign.paste", f"class:{cls.pk} count:{len(found)} blocked:{len(blocked)}")
            return Response({"added": len(found) - len(blocked), "blocked": blocked, "missing": missing, "bad": bad}, status=201)
        return Response({"matched": found, "missing": missing, "bad": bad})


def other_class_of(student, classroom):
    """اگر دانش‌آموز در سال جاری در کلاس دیگری ثبت است، آن کلاس؛ وگرنه None."""
    other = ClassRoom.objects.filter(students=student, academic_year=classroom.academic_year).exclude(pk=classroom.pk).first()
    return other
