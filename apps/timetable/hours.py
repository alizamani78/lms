"""محاسبه‌ی ساعت هر زنگ از ساعت شروع و پایان آن (قاعده‌ی تأییدشده):
- ۳۰ تا ۶۰ دقیقه = ۱ ساعت
- ۶۱ تا ۹۰ دقیقه = ۲ ساعت
- به همین ترتیب: ساعت = سقف(دقیقه ÷ ۶۰)، حداقل ۱.
غیبت: غیبت کل روز = جمع ساعت زنگ‌های آن روز؛ غیبت در یک زنگ = ساعت همان زنگ،
و همچنین تفاضل دقیقه‌ای زنگ (که با تقسیم بر ۶۰ به ساعت و دقیقه تبدیل می‌شود)."""
import datetime as _dt


def minutes_between(start, end):
    """تفاضل پایان و شروع بر حسب دقیقه (اگر پایان قبل از شروع باشد، صفر)."""
    if start is None or end is None:
        return 0
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    return max(0, e - s)


def bell_hours(start, end):
    """ساعت محاسباتی یک زنگ: سقف(دقیقه ÷ ۶۰) با حداقل ۱ ساعت."""
    m = minutes_between(start, end)
    if m <= 0:
        return 0
    return max(1, -(-m // 60))


def hm(minutes):
    """دقیقه به «ساعت:دقیقه» (مثلاً ۹۵ → ۱:۳۵)."""
    minutes = int(minutes or 0)
    return f"{minutes // 60}:{minutes % 60:02d}"


def absence_hours(bells, whole_day=False):
    """مقدار غیبت: کل روز = جمع ساعت زنگ‌ها؛ یک زنگ = ساعت همان زنگ.
    bells: فهرست زنگ‌ها (هر کدام start_time و end_time)."""
    if whole_day:
        return sum(bell_hours(b.start_time, b.end_time) for b in bells)
    return sum(bell_hours(b.start_time, b.end_time) for b in bells)


def day_total_hours(bells):
    """جمع ساعت‌های یک روز از روی زنگ‌ها."""
    return sum(bell_hours(b.start_time, b.end_time) for b in bells)
