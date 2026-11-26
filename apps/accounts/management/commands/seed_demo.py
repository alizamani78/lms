"""Create a small demo dataset: org tree, users for every role, a course, enrollments, a published exam.

    py manage.py seed_demo                 # random password, printed once
    py manage.py seed_demo --password 'Str0ng-Demo-Pass-1' --students 20

Idempotent: safe to run again (existing objects are kept; existing users keep their passwords).
Refuses to run when DEBUG is off, so it cannot seed a production database by accident.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OrgUnit, User
from apps.courses.models import Course, Enrollment
from apps.exams.authoring import write_questions
from apps.exams.models import Exam


class Command(BaseCommand):
    help = "Create demo organisations, users, a course and a published exam (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=None, help="Password for NEW demo users (default: random).")
        parser.add_argument("--students", type=int, default=5, help="Number of demo students (default 5).")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off (not recommended).")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("Refusing to seed demo data while DEBUG is off. Set DJANGO_DEBUG=1 for development.")
        password = opts["password"] or secrets.token_urlsafe(12)
        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError("Weak password: " + " ".join(e.messages))

        prov, _ = OrgUnit.objects.get_or_create(name="خراسان رضوی (نمونه)", kind="province", parent=None)
        reg, _ = OrgUnit.objects.get_or_create(name="منطقه ۱ مشهد (نمونه)", kind="region", parent=prov)
        school, _ = OrgUnit.objects.get_or_create(name="دبیرستان نمونه", kind="school", parent=reg)

        created = []

        def user(username, role, org, **extra):
            u, was_created = User.objects.get_or_create(
                username=username, defaults={"role": role, "org": org, **extra})
            if was_created:
                u.set_password(password)
                u.save(update_fields=["password"])
            created.append((username, role, "new" if was_created else "existing (password unchanged)"))
            return u

        user("demo_super", "super_admin", None, is_staff=True, is_superuser=True)
        user("demo_province", "province_admin", prov)
        user("demo_region", "region_admin", reg)
        user("demo_school", "school_admin", school)
        teacher = user("demo_teacher", "teacher", school, first_name="معلم", last_name="نمونه")
        students = [user(f"demo_s{i}", "student", school, first_name=f"دانش‌آموز {i}", last_name="نمونه")
                    for i in range(1, max(opts["students"], 1) + 1)]

        course, _ = Course.objects.get_or_create(
            org=school, title="ریاضی (نمونه)", defaults={"teacher": teacher})
        for st in students:
            Enrollment.objects.get_or_create(course=course, student=st)

        now = timezone.now()
        exam, exam_created = Exam.objects.get_or_create(
            course=course, title="آزمون نمونه",
            defaults={"starts_at": now - timedelta(hours=1), "ends_at": now + timedelta(days=7),
                      "duration_minutes": 20, "is_published": True, "show_results": True})
        if exam_created:
            write_questions(exam, [
                {"text": "۲ + ۲ چند می‌شود؟", "choices": [
                    {"text": "۳", "is_correct": False}, {"text": "۴", "is_correct": True},
                    {"text": "۵", "is_correct": False}]},
                {"text": "ریشه دوم ۸۱ کدام است؟", "choices": [
                    {"text": "۷", "is_correct": False}, {"text": "۸", "is_correct": False},
                    {"text": "۹", "is_correct": True}]},
                {"text": "۳ × ۷ چند می‌شود؟", "choices": [
                    {"text": "۲۱", "is_correct": True}, {"text": "۲۴", "is_correct": False}]},
            ])
        elif not exam.attempts.exists():        # re-open the window on re-runs
            exam.starts_at, exam.ends_at = now - timedelta(hours=1), now + timedelta(days=7)
            exam.save(update_fields=["starts_at", "ends_at", "updated_at"])

        self.stdout.write(self.style.SUCCESS("Demo data ready.\n"))
        for username, role, state in created:
            self.stdout.write(f"  {username:<16} {role:<15} {state}")
        if any(s == "new" for _, _, s in created):
            self.stdout.write(f"\nPassword for NEW users: {password}")
        self.stdout.write(f"\nOrg ids: province={prov.pk} region={reg.pk} school={school.pk}")
        self.stdout.write(f"Course id={course.pk}  Exam id={exam.pk}")
        self.stdout.write("Demo accounts are for development only; delete them before any real deployment.")
