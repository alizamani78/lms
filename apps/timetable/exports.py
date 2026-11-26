"""خروجی برنامه: Excel (openpyxl) و PDF (reportlab با فونت فارسی).

Excel هیچ فونت یا کتابخانه‌ی اضافه‌ای نمی‌خواهد.
PDF برای متن فارسی به یک فایل فونت TTF نیاز دارد (Vazirmatn-Regular.ttf) که در
apps/core/static/fonts/ گذاشته می‌شود؛ فایل woff2 برای PDF کار نمی‌کند.
"""
import io
import os

from django.conf import settings

from apps.timetable.models import BellPeriod, TimetableEntry

DAY_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]
FONT_TTF = os.path.join(settings.BASE_DIR, "apps", "core", "static", "fonts", "Vazirmatn-Regular.ttf")


def _filtered(version, classroom=None, teacher=None):
    qs = version.entries.select_related("classroom__grade", "subject", "teacher", "period", "workshop")
    if classroom:
        qs = qs.filter(classroom_id=classroom)
    if teacher:
        qs = qs.filter(teacher_id=teacher)
    return qs


def _grid(entries, periods):
    cells = {}
    for e in entries:
        cells[(e.weekday, e.period.number)] = e
    return cells


def export_excel(version, classroom=None, teacher=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    periods = list(BellPeriod.objects.filter(school=version.school).order_by("number"))
    entries = list(_filtered(version, classroom, teacher))
    cells = _grid(entries, periods)
    days = sorted({e.weekday for e in entries}) or [0, 1, 2, 3, 4]
    wb = Workbook()
    ws = wb.active
    ws.title = "برنامه"
    ws.sheet_view.rightToLeft = True
    ws.append([f"برنامه‌ی هفتگی — {version.school.name} — {version.title}"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(days) + 1)
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["زنگ"] + [DAY_NAMES[d] for d in days])
    for c in ws[2]:
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center")
        c.fill = PatternFill("solid", fgColor="E2E8F0")
    for p in periods:
        row = [f"زنگ {p.number}\n{p.start_time:%H:%M}–{p.end_time:%H:%M}"]
        for d in days:
            e = cells.get((d, p.number))
            if e is None:
                row.append("")
                continue
            label = f"{e.subject.name}\n{e.classroom.name}" + (f"\n{e.teacher.get_full_name()}" if e.teacher_id else "")
            if e.workshop_id:
                label += f"\nکارگاه: {e.workshop.name}"
            row.append(label)
        ws.append(row)
        r = ws.max_row
        ws.row_dimensions[r].height = 46
        for ci, d in enumerate(days, start=2):
            e = cells.get((d, p.number))
            if e is not None:
                ws.cell(row=r, column=ci).fill = PatternFill("solid", fgColor=_hex_light(e.subject_id))
        for c in ws[r]:
            c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    ws.column_dimensions["A"].width = 18
    for i in range(2, len(days) + 2):
        ws.column_dimensions[get_column_letter(i)].width = 26
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _hex_light(subject_id):
    from apps.timetable.planner import colour_of
    base = colour_of(subject_id).lstrip("#")
    r, g, b = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
    r, g, b = (r + 255) // 2, (g + 255) // 2, (b + 255) // 2     # رنگ روشن برای خانه‌ها
    return f"{r:02X}{g:02X}{b:02X}"


def _fa(text):
    """متن فارسی برای PDF: اتصال حروف و جهت راست‌به‌چپ."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(str(text)))


def export_pdf(version, classroom=None, teacher=None):
    if not os.path.exists(FONT_TTF):
        raise FileNotFoundError("فونت Vazirmatn-Regular.ttf در apps/core/static/fonts نیست؛ برای PDF لازم است.")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle

    pdfmetrics.registerFont(TTFont("VazirPDF", FONT_TTF))
    st = ParagraphStyle("fa", fontName="VazirPDF", fontSize=8, leading=10, alignment=1)
    head = ParagraphStyle("h", fontName="VazirPDF", fontSize=12, leading=16, alignment=1)
    periods = list(BellPeriod.objects.filter(school=version.school).order_by("number"))
    entries = list(_filtered(version, classroom, teacher))
    cells = _grid(entries, periods)
    days = [0, 1, 2, 3, 4]
    title = f"برنامه‌ی هفتگی — {version.school.name}"
    if entries and classroom:
        title += f" — کلاس {entries[0].classroom.name}"
    if entries and teacher and entries[0].teacher_id:
        title += f" — دبیر {entries[0].teacher.get_full_name()}"
    rows = [[Paragraph(_fa("زنگ"), st)] + [Paragraph(_fa(DAY_NAMES[d]), st) for d in days]]
    styles = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0"))]
    for ri, p in enumerate(periods, start=1):
        row = [Paragraph(_fa(f"زنگ {p.number}"), st)]
        for ci, d in enumerate(days, start=1):
            e = cells.get((d, p.number))
            if e is None:
                row.append("")
                continue
            txt = f"{e.subject.name}\n{e.classroom.name}"
            if e.teacher_id and not teacher:
                txt += f"\n{e.teacher.get_full_name()}"
            if e.workshop_id:
                txt += f"\nکارگاه: {e.workshop.name}"
            row.append(Paragraph(_fa(txt).replace("\n", "<br/>"), st))
            styles.append(("BACKGROUND", (ci, ri), (ci, ri), colors.HexColor("#" + _hex_light(e.subject_id))))
        rows.append(row)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=10 * mm)
    table = Table(rows, colWidths=[28 * mm] + [52 * mm] * len(days), repeatRows=1)
    table.setStyle(TableStyle(styles + [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    doc.build([Paragraph(_fa(title), head), table])
    return buf.getvalue()
