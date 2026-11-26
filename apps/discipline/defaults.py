"""Default disciplinary factors. The values are defaults: teachers, deputies and principals can add factors and the
system administrator can change these defaults (they are shared by all schools while school is empty)."""
DEFAULT_FACTORS = [
    ("تأخیر در ورود به کلاس", "-0.50"),
    ("غیبت موجه", "-0.25"),
    ("غیبت غیرموجه", "-1.00"),
    ("عدم نظافت یا رعایت نکردن بهداشت", "-0.50"),
    ("پاسخگویی نامتعارف به معلم یا کادر", "-1.00"),
    ("بی‌انضباطی در کلاس یا سالن", "-0.75"),
    ("نداشتن وسایل یا کتاب درسی", "-0.25"),
    ("مشارکت فعال و رفتار نمونه", "+0.50"),
    ("کمک به هم‌کلاسی یا کادر", "+0.25"),
]
