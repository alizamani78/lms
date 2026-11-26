# اجرای سامانه روی رایانه ویندوزی (توسعه و آزمایش محلی)

## روش ساده (توصیه می‌شود)
۱. فایل `lms_full_project.zip` را در یک **پوشه خالی و جدید** باز کنید (مثلاً `C:\lms\razavi_lms`).
۲. داخل پوشه، فایل `setup_local.bat` را اجرا کنید. این فایل مراحل زیر را خودکار انجام می‌دهد:
   - بررسی کامل بودن فایل‌ها (`tools\check_install.py`)
   - ساخت محیط مجازی و نصب کتابخانه‌ها
   - ساخت جدول‌ها (`makemigrations` و `migrate`)
   - داده‌های پایه (مقاطع، پایه‌ها، رشته‌ها، استان‌ها، تعطیلات و مناسبت‌ها، عامل‌های انضباطی)
   - ساخت حساب مدیر
   - اجرای سرور روی `http://127.0.0.1:8000/`

## اگر فایل‌ها را تکه‌تکه روی هم باز کرده‌اید
خطاهایی مثل `cannot import name ...` یا `No module named ...` نشانه‌ی فایل‌های ناقص‌اند.
پوشه‌ی جدید بسازید، بسته‌ی کامل را همان‌جا باز کنید و `py tools\check_install.py` را بزنید.

## دستورهای دستی (در صورت نیاز)
```bat
py -m venv C:\venv
C:\venv\Scripts\activate
py -m pip install -r requirements.txt
py manage.py makemigrations
py manage.py migrate
py manage.py seed_catalog
py manage.py seed_provinces
py manage.py seed_counties
py manage.py seed_occasions
py manage.py seed_discipline_defaults
py manage.py createsuperuser
py manage.py runserver
```

## آزمون
```bat
py manage.py test
```
خروجی را برای بررسی ارسال کنید.

## نکته‌ها
- پایگاه داده‌ی محلی `db.sqlite3` فقط برای آزمایش است؛ روی سرور از PostgreSQL استفاده کنید.
- برای ورود، کد ملی یا نام کاربری (فقط مدیران) و رمز کافی است.
- فونت‌های فارسی (Sans Serif، Titr، Zar) در صورت نصب روی ویندوز استفاده می‌شوند.
