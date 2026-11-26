"""Gregorian <-> Jalali (Shamsi) conversion and formatting. Pure arithmetic (33-year cycle), no dependencies.

The arithmetic calendar agrees with the official Iranian calendar for the current era (leap years 1399, 1403, 1408 ...);
very distant years can differ by a day, which is irrelevant for school scheduling."""
from datetime import date, datetime

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
# Iranian week: Saturday = 0 ... Friday = 6
WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
_G_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def gregorian_to_jalali(gy, gm, gd):
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + _G_DAYS[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    month_days = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > month_days[gm]:
        gd -= month_days[gm]
        gm += 1
    return gy, gm, gd


def is_jalali_leap(jy):
    # If the day after 29 Esfand is not Nowruz of the next year, Esfand has a 30th day => leap year.
    return jalali_to_gregorian(jy + 1, 1, 1) != _add_day(jalali_to_gregorian(jy, 12, 29))


def _add_day(g):
    d = date(*g).toordinal() + 1
    r = date.fromordinal(d)
    return (r.year, r.month, r.day)


def jalali_month_days(jy, jm):
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if is_jalali_leap(jy) else 29


def weekday_index(d):
    """Saturday=0 ... Friday=6 (Python's Monday=0 ... Sunday=6)."""
    return (d.weekday() + 2) % 7


def jalali_str(value, with_time=False):
    """'1405/08/04' (or '1405/08/04 14:30'); aware datetimes are converted to the current timezone first."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        from django.utils import timezone
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        s = f"{jy}/{jm:02d}/{jd:02d}"
        return f"{s} {value:%H:%M}" if with_time else s
    jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
    return f"{jy}/{jm:02d}/{jd:02d}"


# ---------------------------------------------------------------------------
# توابع تکمیلی: قالب فارسی با ارقام فارسی (برای سربرگ و برگه‌ی امتحان)
# ---------------------------------------------------------------------------
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_jalali(gy, gm, gd):
    """(سال، ماه، روز) شمسی از میلادی؛ همان gregorian_to_jalali."""
    return gregorian_to_jalali(gy, gm, gd)


def format_jalali(value, with_time=True):
    """تاریخ و ساعت شمسی با ارقام فارسی، مثلاً «۲۳ آبان ۱۴۰۵، ساعت ۱۱:۰۰»."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        from django.utils import timezone
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        text = f"{jd} {MONTHS[jm - 1]} {jy}"
        if with_time:
            text += f"، ساعت {value:%H:%M}"
    else:
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        text = f"{jd} {MONTHS[jm - 1]} {jy}"
    return text.translate(_FA_DIGITS)


def system_date(value):
    """تاریخ به قالب سامانه: «۱۴۰۵/۰۸/۲۳ (2026-11-14)» (شمسی با ارقام فارسی و میلادی در پرانتز)."""
    if value is None or value == "":
        return ""
    d = value.date() if isinstance(value, datetime) else value
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}".translate(_FA_DIGITS) + f" ({d.isoformat()})"

