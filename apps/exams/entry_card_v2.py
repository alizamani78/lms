"""کارت ورود به جلسه به شکل کارت رسمی آزمون (یک کارت برای هر دانش‌آموز، با همه‌ی درس‌های آزمون):
سربرگ وزارتخانه و مرکز آزمون، عنوان آزمون، جدول درس‌ها با روز، تاریخ شمسی، ساعت شروع، شماره صندلی،
مشخصات دانش‌آموز و عکس، QR کد، مرکز آزمون و نشانی آن، تذکرات مهم و امضای مدیر."""
import base64
import html
from pathlib import Path

from django.utils import timezone

from apps.academics.models import ClassRoom
from apps.core.jalali import gregorian_to_jalali
from apps.courses.models import Enrollment
from apps.exams.models import ExamScheduleEntry, ExamSchedule, ExamSeat

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]  # بر اساس weekday() پایتون
REMINDERS = [
    "۱ - همراه داشتن هرگونه وسیله‌ی غیرمجاز از قبیل وسایل ارتباط الکترونیک (تلفن همراه حتی خاموش)، دستگاه‌های حافظه‌دار، ساعت هوشمند، دستبند هوشمند، انگشتر هوشمند و غیره در جلسه‌ی آزمون، طبق قانون رسیدگی به تخلفات و جرائم در آزمون‌ها از مصادیق تخلف محسوب شده و مشمول رسیدگی می‌باشد.",
    "۲ - همراه داشتن کارت ورود به جلسه‌ی آزمون با تأیید مدیر واحد آموزشی، برای ورود به جلسه الزامی است.",
    "۳ - حضور در مرکز آزمون از زمان شروع فرایند آزمون الزامی می‌باشد.",
    "۴ - چنانچه کارت فاقد عکس یا دارای اشکالاتی از جمله اشتباه عکس، عدم وضوح و … یا هرگونه مغایرت باشد، لازم است شخصاً با همراه داشتن یک قطعه عکس ۴×۳ (که در سال جاری تهیه شده) به مدرسه‌ی محل تحصیل مراجعه نمایید.",
    "۵ - توصیه می‌شود روز قبل از آزمون، محل دقیق مرکز آزمون شناسایی شود.",
    "۶ - رعایت پوشش و شئونات اسلامی در مراکز برگزاری آزمون الزامی می‌باشد.",
]
PERSIAN_DIGITS_TRANS = FA


def _fa(x):
    return str(x if x is not None else "").translate(FA)


def _photo(user):
    if not user or not user.photo_id:
        return ""
    from apps.accounts.photos import storage_root
    path = Path(storage_root()) / user.photo.stored_name[:2] / user.photo.stored_name
    if not path.is_file():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _qr(text):
    import io
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=2, border=0)
    return buf.getvalue().decode("utf-8")


CSS = """
@font-face{font-family:"VazirCard";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirCard";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 landscape;margin:8mm}
html,body{font-family:"VazirCard",Tahoma,sans-serif;color:#111;font-size:11.5px;line-height:1.7;margin:0;background:#fff}
.card{border:1.5px solid #333;width:100%;box-sizing:border-box}
.top{display:flex}
.left{flex:1 1 62%;border-left:1px solid #333}
.right{flex:0 0 38%;display:flex;flex-direction:column}
.ttl{text-align:center;font-weight:800;font-size:13px;border-bottom:1px solid #333;padding:4px}
table.exams{width:100%;border-collapse:collapse;font-size:11px}
table.exams th,table.exams td{border:1px solid #444;padding:2px 4px;text-align:center}
table.exams th{background:#e9e9e9;font-weight:800}
table.exams td.nm{text-align:right}
.idrow{display:flex;border-bottom:1px solid #333}
.photo{width:30mm;height:38mm;border-left:1px solid #333;flex:0 0 30mm;display:flex;align-items:center;justify-content:center;overflow:hidden}
.photo img{width:100%;height:100%;object-fit:cover}
.emblem{flex:1 1 auto;text-align:center;font-size:10.5px;padding:6px;border-left:1px solid #333}
.qr{flex:0 0 32mm;padding:4px}
.qr svg{width:28mm;height:28mm;display:block}
.stud{padding:6px 8px;border-bottom:1px solid #333;font-size:12px;line-height:1.9}
.stud b{font-weight:800}
.place{padding:6px 8px;border-bottom:1px solid #333;font-size:11.5px;line-height:1.9}
.bottom{display:flex;border-top:1.5px solid #333}
.reminders{flex:1 1 62%;border-left:1px solid #333;padding:6px 8px;font-size:10px;line-height:1.7}
.reminders b{display:block;text-align:center;font-size:11px}
.sign{flex:0 0 38%;text-align:center;padding:12px 8px;font-size:12px;display:flex;flex-direction:column;justify-content:center}
.page{page-break-after:always}
"""


def _student_block(school, st, seats, classroom, sched, title, room, ident="both"):
    if ident == "national":
        ident_html = f"<b>کد ملی:</b> {_fa(st.national_id or '')}<br>\n"
    elif ident == "code":
        ident_html = f"<b>کد دانش‌آموزی:</b> {_fa(st.student_code or '')}<br>\n"
    else:
        ident_html = f"<b>کد ملی:</b> {_fa(st.national_id or '')} &nbsp; <b>نام پدر:</b> {html.escape(getattr(st, 'father_name', '') or '')}<br>\n<b>شماره دانش‌آموزی:</b> {_fa(st.student_code or '')}<br>\n"
    rows = []
    for i, seat in enumerate(seats, start=1):
        ex = seat.exam
        e = sched.get((classroom.pk if classroom else None, ex.course.subject_id))
        if e is not None:
            d = e.date
            t = e.start_time.strftime("%H:%M")
            wd = WEEKDAYS[d.weekday()]
            jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
            day_txt = f"{jy}/{jm}/{jd}"
        else:
            local = timezone.localtime(ex.starts_at) if ex.starts_at else None
            wd = WEEKDAYS[local.weekday()] if local else ""
            day_txt = (lambda jy_jm_jd: f"{jy_jm_jd[0]}/{jy_jm_jd[1]}/{jy_jm_jd[2]}")(gregorian_to_jalali(local.year, local.month, local.day)) if local else ""
            t = local.strftime("%H:%M") if local else ""
        sub = ex.course.subject
        code = f" ({sub.code})" if sub and getattr(sub, "code", "") else ""
        rows.append(f"<tr><td>{_fa(i)}</td><td class='nm'>{html.escape(ex.course.title)}{html.escape(code)}</td>"
                    f"<td>{wd}</td><td>{_fa(day_txt)}</td><td>{_fa(t)}</td><td>{_fa(seat.number)}</td></tr>")
    body = "".join(rows) or "<tr><td colspan='6'>آزمونی برای این دانش‌آموز نیست.</td></tr>"
    cls_grade = classroom.grade.name if classroom else ""
    field = classroom.study_field.name if classroom and classroom.study_field_id else ""
    year = classroom.academic_year.name if classroom and classroom.academic_year_id else ""
    province = ""
    node = school
    while node is not None and node.kind != "province":
        node = node.parent
    province = node.name if node else ""
    region = school.parent.name if school.parent_id else ""
    venue = ""
    addr = ""
    first_ent = next((e for e in sched.values() if e and e.venue_name), None)
    if first_ent:
        venue, addr = first_ent.venue_name, first_ent.venue_address
    qr_text = f"exam-card:{school.pk}:{st.pk}:{seats[0].number if seats else 0}"
    photo = _photo(st)
    return f"""<div class="page"><div class="card">
<div class="top"><div class="left"><div class="ttl">{html.escape(title)}</div>
<table class="exams"><thead><tr><th>ردیف</th><th>کد و عنوان درس</th><th>روز آزمون</th><th>تاریخ آزمون</th><th>شروع</th><th>شماره صندلی</th></tr></thead>
<tbody>{body}</tbody></table>
<div style="padding:6px 8px;font-size:10.5px">دانش‌آموز با مشخصات فوق تعداد {_fa(len(seats))} درس در این نوبت دارد</div></div>
<div class="right">
<div class="idrow"><div class="photo">{f'<img src="{photo}" alt="">' if photo else '<span>بدون عکس</span>'}</div>
<div class="emblem"><b>جمهوری اسلامی ایران</b><br>وزارت آموزش و پرورش<br>سازمان آموزش و پرورش</div>
<div class="qr">{_qr(qr_text)}</div></div>
<div class="stud"><b>نام و نام خانوادگی:</b> {html.escape(st.get_full_name() or st.username)}<br>
{ident_html}
<b>پایه:</b> {html.escape(cls_grade)} &nbsp; <b>رشته:</b> {html.escape(field)} &nbsp; <b>سال تحصیلی:</b> {_fa(year)}<br>
<b>واحد آموزشی:</b> {html.escape(school.name)}</div>
<div class="place"><b>استان:</b> {html.escape(province)} &nbsp; <b>منطقه:</b> {html.escape(region)}<br>
<b>مرکز آزمون:</b> {html.escape(venue)}<br><b>آدرس مرکز آزمون:</b> {html.escape(addr)}</div>
</div></div>
<div class="bottom"><div class="reminders"><b>تذکرات مهم:</b>{"".join(f"<div>{html.escape(r)}</div>" for r in REMINDERS)}</div>
<div class="sign">مدیر واحد آموزشی<br><br>مهر و امضا</div></div>
</div></div>"""


def build_entry_cards(exam=None, paper="A4", landscape=True, per_page=1, staff=False, room="", photo_size="normal", students=None, school=None, grade=None, ident="both"):
    """کارت‌ها به شکل کارت رسمی: یک کارت برای هر دانش‌آموز (با همه‌ی درس‌های آزمون‌های او در مدرسه)."""
    if exam is not None:
        school = exam.course.org
    seat_qs = ExamSeat.objects.filter(exam__course__org=school).select_related(
        "student", "exam__course__subject", "exam__course__classroom").order_by("student__last_name", "student__id", "exam__starts_at")
    if students:
        seat_qs = seat_qs.filter(student_id__in=students)
    if grade:
        seat_qs = seat_qs.filter(exam__course__classroom__grade_id=grade)
    from .models import ExamSchedule
    _sch = ExamSchedule.objects.filter(school=school, status=ExamSchedule.APPROVED).order_by("-approved_at", "-id").first()
    notes_html = ""
    if _sch is not None and _sch.notes.strip():
        notes_html = ("<div style='border:1px solid #000;padding:4px;margin-top:4px;font-size:9px'><b>تذکرات مهم جلسات امتحان:</b><br>"
                      + html.escape(_sch.notes).replace(chr(10), "<br>") + "</div>")
    by_student = {}
    for seat in seat_qs:
        by_student.setdefault(seat.student_id, []).append(seat)
    if not by_student:
        return None
    # برنامه‌ی تأییدشده‌ی امتحان: تاریخ، ساعت و محل هر (کلاس، درس)
    sched = {}
    for e in ExamScheduleEntry.objects.filter(schedule__school=school, schedule__status=ExamSchedule.APPROVED).select_related("schedule", "classroom"):
        sched[(e.classroom_id, e.subject_id)] = e
    pages = []
    title = f"کارت ورود به جلسه آزمون — {exam.title}" if exam is not None else "کارت ورود به جلسه‌ی آزمون‌های پایه"
    for sid, seats in by_student.items():
        st = seats[0].student
        cls = ClassRoom.objects.filter(school=school, students=st, academic_year__is_current=True).select_related("grade", "study_field", "academic_year").first()
        # برنامه‌ی هر درس بر اساس کلاس دانش‌آموز
        local_sched = {}
        if cls:
            for (cid, subj), e in sched.items():
                if cid == cls.pk:
                    local_sched[(cls.pk, subj)] = e
        pages.append(_student_block(school, st, seats, cls, local_sched, title, room, ident=ident) + notes_html)
    css = CSS
    return ('<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>کارت ورود به جلسه</title>'
            f'<style>{css}</style></head><body>' + "".join(pages) + "</body></html>")
