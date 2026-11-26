"""چاپ گروهی کارنامه: هر دانش‌آموز کارنامه‌ی خودش را در یک صفحه‌ی کامل دارد؛ اندازه‌ی برگه A4 یا A5.
نوع کارنامه: نیم‌سال اول، نیم‌سال دوم، کارنامه‌ی تحصیلی، کارنامه‌ی انضباطی."""
import re

from apps.accounts.models import User

TYPES = {
    "half1": ("card2", "اول", "کارنامه‌ی نیم‌سال اول"),
    "half2": ("card2", "دوم", "کارنامه‌ی نیم‌سال دوم"),
    "card": ("card", None, "کارنامه‌ی تحصیلی"),
    "disc": ("disc", None, "کارنامه‌ی انضباطی"),
    "warn1": ("warn", "اول", "کارنامه‌ی هشدار نیم‌سال اول"),
    "warn2": ("warn", "دوم", "کارنامه‌ی هشدار نیم‌سال دوم"),
}

SIZES = {
    "a4": ("A4 portrait", "190mm", "277mm"),
    "a5": ("A5 portrait", "128mm", "190mm"),
}


def _inner(doc):
    """بدنه‌ی یک صفحه‌ی کامل (بدون <html> و <body>) و استایل‌های آن."""
    styles = "".join(re.findall(r"<style[^>]*>.*?</style>", doc, re.S))
    m = re.search(r"<body[^>]*>(.*)</body>", doc, re.S)
    return styles, (m.group(1) if m else doc)


def body_for(kind, student, term, teacher_id=None):
    if kind == "card2":
        from apps.classbook.gradesheets import TERM_MONTHS
        from apps.classbook.report_card_v2 import build as build2, render_html as render2
        doc = render2(build2(student, TERM_MONTHS.get(term, TERM_MONTHS["اول"]), teacher_id=teacher_id), f"کارنامه‌ی نیم‌سال {term}")
    elif kind == "card":
        from apps.classbook.report_card import build as build_card, render_html as render_card
        from apps.classbook.models import ReportCardNote
        doc = render_card(build_card(student, None, teacher_id=teacher_id))
        styles, inner = _inner(doc)
        note = ReportCardNote.objects.filter(student=student, term="سالانه").first()
        return styles, inner + _note_block(note.text if note else "")
    elif kind == "warn":
        from apps.classbook.warning_card import render_fragment
        from apps.classbook.models import ReportCardNote
        note = ReportCardNote.objects.filter(student=student, term=term).first()
        return "", render_fragment(student, term, note.text if note else "", teacher_id=teacher_id)
    else:
        from apps.classbook.printing import render_disc
        from apps.classbook.models import ReportCardNote
        doc = render_disc(student, None)
        styles, inner = _inner(doc)
        note = ReportCardNote.objects.filter(student=student, term="انضباطی").first()
        return styles, inner + _note_block(note.text if note else "")
    return _inner(doc)


def _note_block(text):
    """توضیحات مدیر، زیر کارنامه چاپ می‌شود (فقط وقتی متنی هست)."""
    if not (text or "").strip():
        return ""
    import html as _h
    return ("<div style='border:1px solid #000;border-radius:6px;padding:4px;margin-top:6px;font-size:11px'>"
            "<b>توضیحات مدیر:</b> " + _h.escape(text).replace(chr(10), "<br>") + "</div>")


def render_bulk(students, type_key, size, teacher_id=None):
    kind, term, title = TYPES[type_key]
    label, w, h = SIZES.get(size, SIZES["a4"])
    css_first = ("<style>.warn-card{font-size:11px;line-height:1.7}.warn-card .t{font-weight:800;font-size:14px;text-align:center;"
                 "border-bottom:1px solid #000;margin-bottom:4px}.warn-card table{width:100%;border-collapse:collapse;margin-top:4px}"
                 ".warn-card th,.warn-card td{border:1px solid #888;padding:2px 4px;text-align:center}"
                 ".warn-card .notes{border:1px solid #000;border-radius:6px;padding:4px;margin-top:6px}</style>")
    pages = []
    for st in students:
        styles, inner = body_for(kind, st, term, teacher_id=teacher_id)
        if not css_first:
            css_first = styles
        pages.append(f"<section class='one-card'>{inner}</section>")
    page_css = (f"@page{{size:{label};margin:6mm}}"
                f".one-card{{box-sizing:border-box;width:100%;height:{h};overflow:hidden;page-break-after:always;break-after:page}}"
                f".one-card:last-child{{page-break-after:auto;break-after:auto}}")
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>{title}</title>"
            f"<style>{page_css}</style>{css_first}</head><body>{''.join(pages) or '<p>دانش‌آموزی انتخاب نشده است.</p>'}</body></html>")
