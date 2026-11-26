"""خروجی برنامه‌ی امتحانی تأییدشده به شکل تقویم: روز و تاریخ در ردیف‌ها، کلاس‌ها در ستون‌ها، امتحان در خانه.
رنگ‌بندی بر اساس پایه (هر پایه یک رنگ) و کلاس (دو تُن در هر پایه) — همان رنگ‌های صفحه‌ی تقویم."""
import html
import io

from apps.core.jalali import system_date

# رنگ‌ها: ۱۲ رنگ برای پایه‌ها؛ تُن روشن‌تر/تیره‌تر برای دو کلاسِ متفاوت در یک پایه
PALETTE = ["#cfe8ff", "#d6f5d6", "#ffe3c2", "#e6d6ff", "#ffd6e0", "#d1f5f0",
           "#fff2b3", "#e2e8f0", "#f8d7da", "#d4edda", "#fde2c4", "#dbe9ff"]
DARK = ["#9fc7f0", "#a6dca6", "#f2bd87", "#c3a9ef", "#f0a9bd", "#9fe0d6",
        "#f0e07a", "#b8c2d0", "#f0a9ad", "#a9d8b5", "#f5c99a", "#b5cdf5"]


def color_for(grade, cls):
    """رنگ یک امتحان: پایه تعیین می‌کند کدام رنگ، کلاس تعیین می‌کند کدام تُن (همان الگوریتم در مرورگر)."""
    h = sum(ord(c) for c in (grade or ""))
    tint = sum(ord(c) for c in (cls or "")) % 2
    return (DARK if tint else PALETTE)[h % 12]


def _grid(sched):
    from .models import ExamScheduleEntry
    qs = ExamScheduleEntry.objects.filter(schedule=sched).select_related("classroom__grade", "subject").order_by("date", "start_time")
    classes = {}
    cells = {}
    dates = []
    for e in qs:
        grade = e.classroom.grade.name if e.classroom.grade_id else ""
        key = e.classroom_id
        classes[key] = (grade, e.classroom.name)
        if e.date not in dates:
            dates.append(e.date)
        cells.setdefault((e.date, key), []).append({
            "subject": e.subject.name, "time": e.start_time.strftime("%H:%M"), "duration": e.duration_minutes,
            "venue": e.venue_name or "", "color": color_for(grade, e.classroom.name)})
    order = sorted(classes.keys(), key=lambda k: (classes[k][0], classes[k][1]))
    return dates, order, classes, cells


def render_html(sched):
    e = html.escape
    dates, order, classes, cells = _grid(sched)
    head = "".join(f"<th>{e(classes[k][0])} — {e(classes[k][1])}</th>" for k in order)
    body = ""
    for d in sorted(dates):
        row = f"<th class='cal-date'>{e(system_date(d))}</th>"
        for k in order:
            items = cells.get((d, k), [])
            if not items:
                row += "<td>—</td>"
                continue
            inner = "".join(
                f"<div class='ex' style='background:{i['color']}'><b>{e(i['subject'])}</b><br>"
                f"{e(i['time'])} — {i['duration']} دقیقه{(' — ' + e(i['venue'])) if i['venue'] else ''}</div>"
                for i in items)
            row += f"<td>{inner}</td>"
        body += f"<tr>{row}</tr>"
    legend = "".join(f"<span class='lg' style='background:{PALETTE[i]}'>پایه‌ی {i + 1}</span>" for i in range(0))
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>تقویم امتحانات</title><style>{CSS}</style></head><body>"
            f"<div class='ttl'>تقویم امتحانات — {e(sched.title)}<br><span style='font-weight:400'>{e(sched.school.name)} — "
            f"از {e(system_date(sched.start_date))} تا {e(system_date(sched.end_date))}</span></div>"
            f"<table><thead><tr><th>روز و تاریخ</th>{head}</tr></thead><tbody>{body}</tbody></table>"
            f"<div class='sign'><div>دبیر آموزش<div class='line'></div></div><div>معاون آموزشی<div class='line'></div></div>"
            f"<div>مدیر مدرسه<div class='line'></div></div></div></body></html>")


CSS = """
@font-face{font-family:"VazirSch";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirSch";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A3 landscape;margin:8mm}
html,body{font-family:"VazirSch",Tahoma,sans-serif;color:#000;font-size:10.5px;margin:0;background:#fff}
.ttl{text-align:center;font-weight:800;font-size:14px;border:1.5px solid #000;padding:6px;margin-bottom:6px}
table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #000;padding:3px;text-align:center;vertical-align:top}
th{background:#eee;font-weight:800}th.cal-date{width:90px}
.ex{border-radius:6px;padding:3px 4px;margin:2px 0;text-align:right}
.sign{display:flex;justify-content:space-between;margin-top:14px}.sign div{width:30%;text-align:center}.line{border-bottom:1px dotted #000;height:24px}
"""


def render_xlsx(sched):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    dates, order, classes, cells = _grid(sched)
    wb = Workbook()
    ws = wb.active
    ws.title = "تقویم امتحانات"
    ws.sheet_view.rightToLeft = True
    ws.append([f"تقویم امتحانات — {sched.title}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["روز و تاریخ"] + [f"{classes[k][0]} — {classes[k][1]}" for k in order])
    for c in ws[2]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, horizontal="center")
    for d in sorted(dates):
        row = [system_date(d)]
        for k in order:
            items = cells.get((d, k), [])
            row.append("\n".join(f"{i['subject']} — {i['time']} ({i['duration']} دقیقه)" + (f" — {i['venue']}" if i["venue"] else "") for i in items))
        ws.append(row)
        r = ws.max_row
        for col_index, k in enumerate(order, start=2):
            items = cells.get((d, k), [])
            if items:
                cell = ws.cell(row=r, column=col_index)
                cell.fill = PatternFill("solid", fgColor=items[0]["color"].lstrip("#"))
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 18
    for i in range(2, 2 + len(order)):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = 28
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
