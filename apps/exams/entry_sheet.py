"""کارت ورود به جلسه (چاپی): راهنمای آزمون، کارت صندلی با عکس و شماره، و کارت عوامل آزمون.
اندازه‌ی برگه، جهت، تعداد کارت در هر برگ و موقعیت‌ها قابل تنظیم است؛ فونت و کادربندی سامانه را دارد."""
import base64
import html
from pathlib import Path

from django.utils import timezone

from apps.core.jalali import format_jalali

PAPER = {"A4": (210, 297), "A5": (148, 210), "Letter": (216, 279)}
MARGIN_MM = 8


def _photo_data_uri(user):
    """عکس دانش‌آموز از فضای خصوصی، به‌صورت داده‌ی درون‌خطی (بدون نیاز به ورود دوباره)."""
    if not user or not user.photo_id:
        return ""
    from apps.accounts.photos import storage_root
    path = Path(storage_root()) / user.photo.stored_name[:2] / user.photo.stored_name
    if not path.is_file():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _css(paper, landscape, per_page):
    w, h = PAPER.get(paper, PAPER["A4"])
    if landscape:
        w, h = h, w
    cols = 2 if per_page >= 2 else 1
    return f"""
@page{{size:{w}mm {h}mm;margin:{MARGIN_MM}mm}}
html,body{{margin:0;font-family:"VazirEntry",Tahoma,sans-serif;color:#111;font-size:12.5px;line-height:1.8;background:#fff}}
@font-face{{font-family:"VazirEntry";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}}
@font-face{{font-family:"VazirEntry";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}}
.page{{display:grid;grid-template-columns:repeat({cols},1fr);gap:6mm;page-break-after:always;align-content:start}}
.card{{border:2px solid #111;border-radius:10px;padding:8px 10px;box-sizing:border-box;min-height:{(h-2*MARGIN_MM)/ (2 if per_page>=2 else 1) - 8:.0f}mm}}
.card h2{{margin:0 0 4px;font-size:15px;text-align:center}}
.title{{text-align:center;font-weight:800;border-bottom:2px dashed #111;padding-bottom:4px;margin-bottom:6px}}
table{{width:100%;border-collapse:collapse;font-size:12px}} td{{border:1px solid #888;padding:3px 5px}} td.k{{width:34%;color:#333;background:#f3f3f3}}
.ph{{width:28mm;height:36mm;border:1px solid #555;margin:0 auto 6px;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.ph img{{width:100%;height:100%;object-fit:cover}}
.num{{text-align:center;margin:6px 0;font-size:12px}} .num b{{font-size:30px;display:block;letter-spacing:2px}}
.rules{{padding-right:16px;margin:4px 0;font-size:12px}} .ins{{font-size:12px;margin-top:6px}}
.staff table td{{font-size:12px}} .sign{{height:26px}}
"""


def build_entry_cards(exam, paper="A4", landscape=False, per_page=2, staff=True, room="", photo_size="normal", students=None):
    """students: فهرست شناسه‌ی دانش‌آموزان (اختیاری). اگر داده شود، فقط کارت‌های همان‌ها چاپ می‌شود."""
    from .models import ExamSeat
    course = exam.course
    school = course.org
    seat_qs = ExamSeat.objects.filter(exam=exam).select_related("student").order_by("student__last_name", "student__id")
    if students:
        seat_qs = seat_qs.filter(student_id__in=students)
    seats = {s.student_id: s for s in seat_qs}
    enrolled = [type("E", (), {"student": s.student})() for s in seat_qs]
    start = timezone.localtime(exam.starts_at) if exam.starts_at else None
    date_txt = format_jalali(exam.starts_at, with_time=False) if exam.starts_at else ""
    time_txt = start.strftime("%H:%M") if start else ""
    # برنامه‌ی تأییدشده‌ی امتحان: تاریخ، ساعت و محل برگزاری هر کلاس (اگر در برنامه باشد)
    from apps.exams.models import ExamScheduleEntry, ExamSchedule
    sched = {e.classroom_id: e for e in ExamScheduleEntry.objects.filter(
        schedule__status=ExamSchedule.APPROVED, subject=course.subject_id).select_related("classroom__grade")} if course.subject_id else {}
    teacher = (course.teacher.get_full_name() or course.teacher.username) if course.teacher_id else ""
    total = sum(float(q.points) for q in exam.questions.all())
    esc = lambda x: html.escape(str(x if x is not None else ""))
    pages = []
    cards = []
    for e in enrolled:
        st = e.student
        seat = seats.get(st.pk)
        if seat is None:
            continue
        ph = _photo_data_uri(st)
        from apps.academics.models import ClassRoom
        cls = ClassRoom.objects.filter(school=school, students=st, academic_year__is_current=True).select_related("grade", "study_field").first()
        ent = sched.get(cls.pk) if cls else None
        d_txt = format_jalali(ent.date, with_time=False) if ent else date_txt
        t_txt = ent.start_time.strftime("%H:%M") if ent else time_txt
        dur = ent.duration_minutes if ent else exam.duration_minutes
        venue = ent.venue_name if ent else ""
        address = ent.venue_address if ent else ""
        field_txt = cls.study_field.name if cls and cls.study_field_id else ""
        cls_txt = f"{cls.grade.name} — {cls.name}" if cls else ""
        from .header import _qr_svg
        qr = _qr_svg(f"exam:{exam.pk}:seat:{seat.number}")
        cards.append(
            f'<div class="card"><div class="title">راهنمای آزمون</div>'
            f'<table><tr><td class="k">آزمون</td><td>{esc(exam.title)}</td></tr>'
            f'<tr><td class="k">درس</td><td>{esc(course.title)}</td></tr>'
            f'<tr><td class="k">مدرسه</td><td>{esc(school.name)}</td></tr>'
            f'<tr><td class="k">تاریخ / ساعت</td><td>{esc(d_txt)} — {esc(t_txt)}</td></tr>'
            f'<tr><td class="k">مدت آزمون</td><td>{esc(dur)} دقیقه</td></tr>'
            f'<tr><td class="k">محل برگزاری</td><td>{esc(venue)}</td></tr>'
            f'<tr><td class="k">نشانی</td><td>{esc(address)}</td></tr>'
            f'<tr><td class="k">بارم کل</td><td>{esc(f"{total:g}")}</td></tr>'
            f'<tr><td class="k">دبیر</td><td>{esc(teacher)}</td></tr></table>'
            f'<div class="rules"><div>کارت صندلی را همراه داشته باشید و در جای خود بنشینید.</div>'
            f'<div>پیش از شروع، ساعت و مدت آزمون را بررسی کنید.</div>'
            f'<div>پاسخ‌ها پس از ثبت نهایی قابل تغییر نیستند.</div></div>'
            + (f'<div class="ins">{esc(exam.instructions)}</div>' if exam.instructions else "")
            + '</div>'
        )
        ph_style = " style=\"width:22mm;height:28mm\"" if photo_size == "small" else ""
        cards.append(
            f'<div class="card seat"><div class="ph"{ph_style}>' + (f'<img src="{ph}" alt="">' if ph else "<span>بدون عکس</span>") + '</div>'
            f'<div class="num">شماره‌ی داوطلبی<b>{esc(seat.number)}</b></div>'
            f'<table><tr><td class="k">نام و نام خانوادگی</td><td>{esc(st.get_full_name())}</td></tr>'
            f'<tr><td class="k">شماره‌ی دانش‌آموزی</td><td>{esc(st.student_code)}</td></tr>'
            f'<tr><td class="k">کلاس</td><td>{esc(cls_txt)}</td></tr>'
            f'<tr><td class="k">رشته</td><td>{esc(field_txt)}</td></tr>'
            f'<tr><td class="k">جایگاه</td><td>{esc(seat.room or room)}</td></tr>'
            f'<tr><td class="k">آزمون</td><td>{esc(exam.title)}</td></tr></table>'
            f'<div class="qr">{qr}</div></div>'
        )
    if not cards:
        return None
    # دو کارت هر دانش‌آموز (راهنما + صندلی) در هر برگ به تعداد per_page
    per = 2 if per_page >= 2 else 1
    for i in range(0, len(cards), per):
        pages.append('<div class="page">' + "".join(cards[i:i + per]) + "</div>")
    if staff:
        pages.append(
            '<div class="page"><div class="card staff"><div class="title">کارت عوامل آزمون</div>'
            f'<table><tr><td class="k">آزمون</td><td>{esc(exam.title)} — {esc(course.title)}</td></tr>'
            f'<tr><td class="k">مدرسه</td><td>{esc(school.name)}</td></tr>'
            f'<tr><td class="k">تاریخ / ساعت</td><td>{esc(date_txt)} — {esc(time_txt)}</td></tr>'
            f'<tr><td class="k">مدت آزمون</td><td>{esc(exam.duration_minutes)} دقیقه</td></tr>'
            f'<tr><td class="k">تعداد داوطلب</td><td>{len([c for c in cards if "seat" in c])}</td></tr>'
            f'<tr><td class="k">دبیر درس</td><td>{esc(teacher)}</td></tr></table>'
            '<h2 style="margin-top:10px">ناظر و مسئول جلسه</h2>'
            '<table><tr><td class="k">نام ناظر</td><td class="sign"></td><td class="k">امضا</td><td class="sign"></td></tr>'
            '<tr><td class="k">نام ناظر</td><td class="sign"></td><td class="k">امضا</td><td class="sign"></td></tr></table>'
            '<div class="ins">شماره‌ی داوطلبی و شماره‌ی دانش‌آموزی باید با کارت صندلی یکی باشند.</div></div></div>'
        )
    css = _css(paper, landscape, per)
    return ('<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>کارت ورود به جلسه</title>'
            f'<style>{css}</style></head><body>' + "".join(pages) + '</body></html>')
