"""رضایت‌نامه‌ی اردو و مسابقات، مطابق متن الگوی چاپی مدرسه؛ همه‌ی فیلدها پر می‌شوند:
نام ولی، مقصد، نوع برنامه (اردو/بازدید…)، مدت، تاریخ و ساعت اعزام، محل شروع، تاریخ برگشت،
کلاس، نام دانش‌آموز، تلفن، و وضعیت رضایت. بخش دوم برای مدیریت مدرسه با موارد قابل ذکر."""
import html

from apps.core.jalali import system_date

CSS = """
@font-face{font-family:"VazirCons";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirCons";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
html,body{font-family:"VazirCons",Tahoma,sans-serif;color:#000;margin:0;background:#fff}
.page{box-sizing:border-box;overflow:hidden}
.a5 .page{height:auto;font-size:10.5px;line-height:1.75}
.a4l .page{font-size:11.5px;line-height:1.8;column-count:2;column-gap:12mm;column-fill:auto;height:182mm}
.a4l .page .sec{break-inside:avoid}
.c{text-align:center}.b{font-weight:800}
.title{display:inline-block;border:1.5px solid #000;border-radius:6px;padding:4px 40px;font-weight:800;font-size:16px;margin:4px auto 10px}
.ln{display:inline-block;border-bottom:1px dotted #000;min-width:120px;padding:0 6px;text-align:center;font-weight:700}
.ln.w{min-width:260px}
.dot{display:inline-block;width:11px;height:11px;border:1.2px solid #000;border-radius:50%;vertical-align:middle;margin:0 4px 0 8px;position:relative}
.dot.on::after{content:"";position:absolute;inset:2px;border-radius:50%;background:#000}
.opts{white-space:nowrap}
.sep{border-top:1px dashed #000;margin:14px 0}
.small{font-size:11.5px}
.rem{display:block;min-width:0;height:34px;border-bottom:1px dotted #000}
"""

TYPES = [("excursion", "اردو"), ("science_visit", "بازدید"), ("cultural", "فرهنگی"), ("tourism", "سیاحتی"),
         ("pilgrimage", "زیارتی"), ("basij", "بسیج"), ("sport", "ورزشی"), ("rahian", "راهیان نور")]


def _v(x):
    return html.escape(str(x if x not in (None, "") else ""))


def _radios(selected, options):
    return " &nbsp; ".join(
        f"<span class='opts'><span class='dot{' on' if key == selected else ''}'></span>{html.escape(label)}</span>"
        for key, label in options)


def _date(v):
    import datetime as dt
    try:
        return system_date(dt.date.fromisoformat(str(v)[:10]))
    except (ValueError, TypeError):
        return ""


def render(d, participant, size="a5"):
    consent = getattr(participant, "consent", "") if participant else ""
    accepted = consent == "accepted"
    dep = d.get("departure_at") or ""
    dep_date = _date(dep[:10]) if dep else _date(d.get("start_date"))
    dep_time = dep[11:16] if len(dep) >= 16 else ""
    end_date = _date(d.get("end_date"))
    trip_type = d.get("trip_type_key") or "excursion"
    guardian = _v(d.get("guardian_name"))
    student = _v(d.get("student"))
    klass = _v(d.get("class_name"))
    dest = _v(d.get("destination"))
    place = _v(d.get("departure_place"))
    days = _v(d.get("duration_days") or "")
    trip = _v(d.get("trip"))
    phone = _v(d.get("phone"))
    deadline = _date(d.get("consent_deadline")) if d.get("consent_deadline") else ""
    body = f"""
<div class='page'>
<div class='sec c b' style='font-size:14px'>باسمه تعالی</div>
<div class='c'><span class='title'>« رضایت‌نامه »</span></div>
<div>ولی محترم دانش‌آموز: <span class='ln w'>{guardian}</span></div>
<div class='b'>با سلام و احترام</div>
<div>این مدرسه در نظر دارد برنامه‌ی {_radios(trip_type, TYPES)} به مقصد <span class='ln'>{dest}</span>
که به تصویب شورای مدرسه رسیده است، به مدت <span class='ln'>{days}</span> روز،
در روز <span class='ln'>{dep_date}</span> از ساعت <span class='ln'>{dep_time}</span> لغایت <span class='ln'>{end_date}</span>
از محل <span class='ln'>{place}</span> برگزار نماید.</div>
<div style='margin-top:6px'>چنانچه با شرکت فرزندتان در این برنامه موافقت دارید، رضایت‌نامه‌ی زیر را تکمیل و تا تاریخ
<span class='ln'>{deadline}</span> به مدرسه تحویل فرمایید. ضمناً برنامه‌های پیش‌بینی‌شده به شرح پیوست اعلام می‌گردد.</div>
<div>خواهشمند است چنانچه فرزند شما به بیماری خاصی مبتلاست یا دارو مصرف می‌کند، و یا نکته‌ای در مورد ایشان مدنظر است
که سرپرست و مربیان اردو باید در جریان باشند، در قسمت <b>موارد قابل ذکر</b> مرقوم فرمایید.</div>
<div class='small'><span class='b'>توضیح:</span> مدرسه تنها در قبال آنچه که شما قید می‌فرمایید مسئولیت دارد.</div>
<div>کلاس: <span class='ln'>{klass}</span> &nbsp; پایه: <span class='ln'>{_v(d.get('grade_name'))}</span></div>
<div style='margin-top:8px'>نام و نام خانوادگی مدیر: <span class='ln w'>{_v(d.get('principal'))}</span></div>
<div>مهر و امضای مدیر مدرسه: <span class='ln w'></span></div>
<div class='sep'></div>
<div class='c b'>باسمه تعالی</div>
<div class='c b'>مدیریت محترم مدرسه</div>
<div>با سلام، اینجانب <span class='ln'>{guardian}</span> ولی دانش‌آموز <span class='ln'>{student}</span> کلاس <span class='ln'>{klass}</span></div>
<div>دوره‌ی <span class='ln'>{trip}</span> رضایت کامل خودم را مبنی بر حضور فرزندم در برنامه‌ی
{_radios(trip_type, TYPES[:2])} <span class='dot {"on" if accepted else ""}'></span> اعلام می‌دارم.</div>
<div style='margin-top:6px'>موارد قابل ذکر:</div>
<div class='rem'>۱.</div>
<div class='rem'>۲.</div>
<div style='margin-top:6px'>نام و نام خانوادگی ولی دانش‌آموز: <span class='ln w'>{guardian}</span></div>
<div>امضاء و اثرانگشت ولی دانش‌آموز: <span class='ln w'></span></div>
<div>تلفن تماس: <span class='ln w'>{phone}</span></div>
</div>
"""
    if size == "a4l":
        page_css = "@page{size:A4 landscape;margin:8mm}"
        cls = "a4l"
    else:
        page_css = "@page{size:A5 portrait;margin:7mm}"
        cls = "a5"
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>رضایت‌نامه</title><style>{page_css}{CSS}</style></head>"
            f"<body class='{cls}'>{body}</body></html>")
