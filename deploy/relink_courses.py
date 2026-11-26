"""اتصال درس‌های کلاس (از سیدا) به «درس» اصلی در بانک درس‌ها.

درس‌های وارد‌شده از سیدا گاهی فقط عنوان دارند و به درس اصلی وصل نیستند؛ برنامه‌ی امتحانی هم درسی را
که به درس اصلی وصل نباشد نمی‌تواند بگذارد. این فرمان:
  - برای هر درس کلاس سال جاری که به درس اصلی وصل نیست، درس هم‌نام را پیدا و وصل می‌کند
    (کد سیدا مثل «14031-» حذف می‌شود و حروف عربی/فارسی یکسان حساب می‌شوند).
  - با --create-missing اگر درس هم‌نامی نبود، آن را در بانک درس‌های اصلی می‌سازد (یک بار؛ تکراری ساخته نمی‌شود).
  - پیش‌فرض فقط نشان می‌دهد؛ با --apply اجرا می‌شود.

  set -a; . /etc/razavi-lms/.env; set +a
  /opt/razavi-lms/venv/bin/python deploy/relink_courses.py                       # فقط گزارش
  /opt/razavi-lms/venv/bin/python deploy/relink_courses.py --apply               # وصل کردن
  /opt/razavi-lms/venv/bin/python deploy/relink_courses.py --apply --create-missing
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from apps.academics.models import AcademicYear, Subject  # noqa: E402
from apps.courses.models import Course  # noqa: E402


def norm(text):
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("‌", " ")
    t = re.sub(r"^\s*\d+\s*[-–]\s*", "", t)          # کد سیدا: «14031- …»
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def new_code(title):
    """کد یکتا برای درس تازه: کد سیدا اگر در عنوان بود، وگرنه شماره‌ی بعدی آزاد."""
    m = re.match(r"^\s*(\d+)\s*[-–]", title or "")
    if m and not Subject.objects.filter(code=m.group(1)).exists():
        return m.group(1)[:20]
    n = Subject.objects.count() + 1
    while Subject.objects.filter(code=f"S{n:05d}").exists():
        n += 1
    return f"S{n:05d}"


def main():
    apply = "--apply" in sys.argv
    create = "--create-missing" in sys.argv
    years = AcademicYear.objects.filter(is_current=True)
    courses = Course.objects.filter(subject__isnull=True, academic_year__in=years).select_related("org")
    index = {}
    for s in Subject.objects.all():
        index.setdefault(norm(s.name), s)
    linked, created, missing = 0, 0, []
    for c in courses:
        key = norm(c.title)
        target = index.get(key)
        if target is None and create:
            name = re.sub(r"^\s*\d+\s*[-–]\s*", "", c.title).strip()[:150]
            target = Subject(name=name, difficulty=3, code=new_code(c.title))
            if apply:
                target.save()
            index[key] = target
            created += 1
        if target is None:
            missing.append(c.title)
            continue
        if apply and target.pk:
            c.subject = target
            c.save(update_fields=["subject"])
        linked += 1
    print(f"درس‌های کلاس سال جاری بدون درس اصلی: {courses.count()}")
    print(f"  وصل‌شدنی: {linked}  |  ساخته‌شدنی در بانک: {created}  |  بدون پیدا شدن: {len(missing)}")
    for m in sorted(set(missing))[:60]:
        print("   ✗", m)
    if not apply:
        print("\nهنوز چیزی تغییر نکرد. با --apply اجرا کنید.")
    else:
        print("\nانجام شد.")


if __name__ == "__main__":
    main()
