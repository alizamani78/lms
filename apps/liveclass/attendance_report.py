"""گزارش حضور و غیاب کلاس آنلاین: هر شرکت‌کننده با نام، نام خانوادگی، کد ملی، نقش، زمان ورود و خروج، و مدت حضور.
داده از رویدادهای ورود و خروج سرور (OnlinePresence) خوانده می‌شود."""
import html
import io

from apps.core.jalali import format_jalali
from apps.courses.roster import roster_ids

ROLE_LABEL = {"student": "دانش‌آموز", "teacher": "معلم", "school_admin": "مدیر مدرسه",
              "school_deputy_edu": "معاون آموزشی", "school_deputy_exec": "معاون اجرایی",
              "school_deputy_culture": "معاون پرورشی", "super_admin": "مدیر سیستم"}


def _dt(v):
    if not v:
        return ""
    return format_jalali(v).replace("،", "")


def _fmt_secs(n):
    n = int(n or 0)
    return f"{n // 3600:02d}:{(n % 3600) // 60:02d}:{n % 60:02d}"


def build(session):
    from .models import OnlinePresence
    rows = []
    for p in OnlinePresence.objects.filter(session=session).select_related("student").order_by("first_join", "id"):
        u = p.student
        left = "" if p.open_since else _dt(p.last_seen)
        rows.append({
            "first_name": u.first_name or "", "last_name": u.last_name or u.username,
            "national_id": u.national_id or "", "role": ROLE_LABEL.get(u.role, u.role),
            "join": _dt(p.first_join), "leave": left or ("در جلسه" if p.open_since else ""),
            "duration": _fmt_secs(p.total_seconds),
        })
    return rows


def render_xlsx(session, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "حضور و غیاب"
    ws.sheet_view.rightToLeft = True
    ws.append([f"گزارش حضور و غیاب — {session.title}", "", "", "", "", "", "", ""])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["نام", "نام خانوادگی", "کد ملی", "نقش", "تاریخ و ساعت حضور", "تاریخ و ساعت خروج", "مدت حضور"])
    for c in ws[2]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r["first_name"], r["last_name"], r["national_id"], r["role"], r["join"], r["leave"], r["duration"]])
    ws.column_dimensions["E"].width = 24
    ws.column_dimensions["F"].width = 24
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CSS = """
@font-face{font-family:"VazirAtt";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirAtt";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:9mm}
html,body{font-family:"VazirAtt",Tahoma,sans-serif;color:#000;font-size:11px;margin:0;background:#fff}
.ttl{text-align:center;font-weight:800;font-size:13px;border:1.5px solid #000;padding:6px;margin-bottom:6px}
table{width:100%;border-collapse:collapse}th,td{border:1px solid #000;padding:3px 4px;text-align:center}
th{background:#eee;font-weight:800}td.r{text-align:right}
.sign{display:flex;justify-content:space-between;margin-top:14px}.sign div{width:31%;text-align:center}.line{border-bottom:1px dotted #000;height:24px}
"""


def render_html(session, rows):
    e = html.escape
    body = "".join(
        f"<tr><td>{i}</td><td class='r'>{e(r['first_name'])}</td><td class='r'>{e(r['last_name'])}</td>"
        f"<td>{e(r['national_id'])}</td><td>{e(r['role'])}</td><td>{e(r['join'])}</td><td>{e(r['leave'])}</td><td>{e(r['duration'])}</td></tr>"
        for i, r in enumerate(rows, start=1)) or "<tr><td colspan='8'>شرکت‌کننده‌ای ثبت نشده است.</td></tr>"
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>گزارش حضور</title><style>{CSS}</style></head><body>"
            f"<div class='ttl'>گزارش حضور و غیاب کلاس آنلاین — {e(session.title)}<br>"
            f"<span style='font-weight:400'>{e(session.course.title)} — {e(session.course.org.name)}</span></div>"
            f"<table><thead><tr><th>ردیف</th><th>نام</th><th>نام خانوادگی</th><th>کد ملی</th><th>نقش</th>"
            f"<th>تاریخ و ساعت حضور</th><th>تاریخ و ساعت خروج</th><th>مدت حضور</th></tr></thead><tbody>{body}</tbody></table>"
            f"<div class='sign'><div>دبیر<div class='line'></div></div><div>مدیر<div class='line'></div></div><div>معاون آموزشی<div class='line'></div></div></div>"
            f"</body></html>")
