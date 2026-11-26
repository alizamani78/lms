"""پاک‌سازی مدرسه‌ی اشتباه در کارتابل و حذف امن مدرسه.
۱) حذف عضویت یک کاربر از یک مدرسه (کارتابل): python manage.py school_cleanup unlink --user <username> --school <id> [--apply]
۲) حذف مدرسه: python manage.py school_cleanup delete --school <id> [--apply]
   اگر مدرسه داده (کلاس، درس، دانش‌آموز، برنامه، کاربر وابسته) داشته باشد، حذف انجام نمی‌شود مگر --force-data.
   پیش‌نمایش پیش‌فرض است؛ با --apply اجرا می‌شود. قبل از اجرا پشتیبان بگیرید."""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "حذف عضویت کاربر از یک مدرسه، یا حذف امن یک مدرسه."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["unlink", "delete"])
        parser.add_argument("--school", type=int, required=True)
        parser.add_argument("--user", default="")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--force-data", action="store_true")

    def handle(self, *args, **o):
        from apps.accounts.models import OrgUnit, SchoolMembership, User
        school = OrgUnit.objects.filter(pk=o["school"], kind="school").first()
        if school is None:
            raise CommandError("مدرسه پیدا نشد.")
        mode = "اجرا" if o["apply"] else "پیش‌نمایش (چیزی تغییر نکرد)"
        if o["action"] == "unlink":
            if not o["user"]:
                raise CommandError("--user لازم است.")
            u = User.objects.filter(username=o["user"]).first()
            if u is None:
                raise CommandError("کاربر پیدا نشد.")
            qs = SchoolMembership.objects.filter(user=u, school=school)
            self.stdout.write(f"{mode}: {qs.count()} عضویت «{u.username}» از «{school.name}» حذف می‌شود.")
            if o["apply"]:
                qs.delete()
                self.stdout.write("انجام شد.")
            return
        # delete the school: count what hangs on it first
        from apps.academics.models import ClassRoom
        from apps.courses.models import Course
        from apps.timetable.models import TimetableVersion
        counts = {
            "کلاس": ClassRoom.objects.filter(school=school).count(),
            "درس": Course.objects.filter(org=school).count(),
            "دانش‌آموز/کاربر وابسته": User.objects.filter(org=school).count(),
            "نسخه‌ی برنامه": TimetableVersion.objects.filter(school=school).count(),
            "عضویت": SchoolMembership.objects.filter(school=school).count(),
        }
        self.stdout.write(f"{mode}: مدرسه «{school.name}» (شماره {school.pk}). وابستگی‌ها: {counts}")
        has_data = any(v for k, v in counts.items() if k != "عضویت")
        if has_data and not o["force_data"]:
            raise CommandError("این مدرسه داده دارد؛ حذف نشد. اگر مطمئنید، با --force-data و پشتیبان دوباره اجرا کنید.")
        if o["apply"]:
            school.delete()
            self.stdout.write("مدرسه حذف شد.")
