"""سربرگ برگه‌ی امتحان: فیلدهایی که مدیر یا معاون آموزشی انتخاب و ترتیب می‌کند.

layout = {"fields": [{"key": "...", "enabled": true, "row": 1, "order": 1, "align": "right"}, ...], "quote": "..."}
row   : ردیف سربرگ (۱ تا ۴)؛ order: ترتیب در همان ردیف؛ align: right | center | left
"""
import html
from datetime import datetime

from django.utils import timezone

# کلید، برچسب و نوع (text | qr | quote | description)
FIELDS = [
    ("student_name", "نام و نام خانوادگی دانش‌آموز", "text"),
    ("student_code", "شماره دانش‌آموزی", "text"),
    ("candidate_no", "شماره داوطلبی", "text"),
    ("teacher", "نام دبیر", "text"),
    ("school", "نام مدرسه", "text"),
    ("region", "نام منطقه", "text"),
    ("province", "نام استان", "text"),
    ("datetime", "تاریخ و زمان شروع آزمون", "text"),
    ("duration", "مدت زمان آزمون", "text"),
    ("qr", "QR کد", "qr"),
    ("subject", "نام درس", "text"),
    ("classroom", "نام کلاس", "text"),
    ("exam_hall", "نام حوزه‌ی امتحانی", "text"),
    ("level_branch", "مقطع به همراه شاخه", "text"),
    ("field", "رشته", "text"),
    ("grade", "پایه تحصیلی", "text"),
    ("quote", "جمله‌ی قصار", "quote"),
    ("description", "توضیحات آزمون", "description"),
]
KEYS = [k for k, _, _ in FIELDS]
ALIGNS = ("right", "center", "left")
PAPERS = {"A4": "210mm 297mm", "A5": "148mm 210mm", "Letter": "8.5in 11in"}
DEFAULT_LAYOUT = {"fields": [
    {"key": "school", "enabled": True, "row": 1, "order": 1, "align": "center"},
    {"key": "subject", "enabled": True, "row": 2, "order": 1, "align": "right"},
    {"key": "classroom", "enabled": True, "row": 2, "order": 2, "align": "center"},
    {"key": "teacher", "enabled": True, "row": 2, "order": 3, "align": "left"},
    {"key": "student_name", "enabled": True, "row": 3, "order": 1, "align": "right"},
    {"key": "student_code", "enabled": True, "row": 3, "order": 2, "align": "center"},
    {"key": "candidate_no", "enabled": True, "row": 3, "order": 3, "align": "left"},
    {"key": "datetime", "enabled": True, "row": 4, "order": 1, "align": "right"},
    {"key": "duration", "enabled": True, "row": 4, "order": 2, "align": "center"},
    {"key": "qr", "enabled": True, "row": 4, "order": 3, "align": "left"},
], "quote": ""}


def clean_layout(raw):
    """یک سربرگ معتبر: فقط کلیدهای شناخته‌شده، ردیف ۱ تا ۴، ترتیب عددی، تراز درست."""
    if not isinstance(raw, dict):
        raise ValueError("ساختار سربرگ نامعتبر است.")
    out = []
    seen = set()
    for f in raw.get("fields") or []:
        key = str(f.get("key", ""))
        if key not in KEYS or key in seen:
            continue
        seen.add(key)
        row = int(f.get("row") or 1)
        out.append({"key": key, "enabled": bool(f.get("enabled", True)),
                    "row": min(4, max(1, row)), "order": max(1, int(f.get("order") or 1)),
                    "align": f.get("align") if f.get("align") in ALIGNS else "right"})
    paper = raw.get("paper") if raw.get("paper") in PAPERS else "A4"
    try:
        width = int(raw.get("width") or 100)
    except (TypeError, ValueError):
        width = 100
    return {"fields": out, "quote": str(raw.get("quote") or "")[:300], "paper": paper, "width": min(100, max(50, width))}


def layout_for(school):
    from .models import ExamHeaderLayout
    obj = ExamHeaderLayout.objects.filter(school=school).first()
    return obj.layout if obj else DEFAULT_LAYOUT


def _qr_svg(text):
    import io
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=3, border=1)
    return buf.getvalue().decode("utf-8")


def _value(key, ctx):
    ex, st, school = ctx["exam"], ctx.get("student"), ctx["school"]
    course = ex.course
    if key == "student_name":
        return st.get_full_name() if st else ""
    if key == "student_code":
        return (st.student_code or "") if st else ""
    if key == "candidate_no":
        return str(ctx.get("candidate_no") or "")
    if key == "teacher":
        return (course.teacher.get_full_name() or course.teacher.username) if course.teacher_id else ""
    if key == "school":
        return school.name
    if key == "region":
        p = school.parent
        return p.name if p and p.kind == "region" else (p.parent.name if p and p.parent_id and p.parent.kind == "region" else "")
    if key == "province":
        node = school
        while node is not None and node.kind != "province":
            node = node.parent
        return node.name if node else ""
    if key == "datetime":
        from apps.core.jalali import format_jalali          # تاریخ و ساعت شمسی
        return format_jalali(ex.starts_at) if ex.starts_at else ""
    if key == "duration":
        return f"{ex.duration_minutes} دقیقه"
    if key == "subject":
        return course.title
    if key == "classroom":
        cls = ctx.get("classroom")
        return cls.name if cls else ""
    if key == "exam_hall":
        return ctx.get("exam_hall", "")
    if key == "level_branch":
        cls = ctx.get("classroom")
        if cls is None:
            return ""
        lvl = cls.grade.level.name
        br = getattr(cls.grade, "branch", None)
        return f"{lvl} — {br.name}" if br else lvl
    if key == "field":
        cls = ctx.get("classroom")
        return cls.study_field.name if cls and cls.study_field_id else ""
    if key == "grade":
        cls = ctx.get("classroom")
        return cls.grade.name if cls else ""
    if key == "quote":
        return ctx.get("quote", "")
    if key == "description":
        return ex.instructions or ""
    return ""


def render_header(ctx, layout=None):
    """HTML سربرگ: هر ردیف با فیلدهای فعال‌اش، به ترتیب و با تراز انتخاب‌شده."""
    layout = layout or DEFAULT_LAYOUT
    labels = {k: (l, t) for k, l, t in FIELDS}
    rows = {1: [], 2: [], 3: [], 4: []}
    final = []   # جمله‌ی قصار و توضیحات: همیشه در ردیف پایانی و وسط‌چین
    for f in sorted(layout["fields"], key=lambda x: (x["row"], x["order"])):
        if not f["enabled"]:
            continue
        if labels[f["key"]][1] in ("quote", "description"):
            final.append(f)
            continue
        rows[f["row"]].append(f)
    out = ['<div class="exam-header">']
    for r in sorted(rows):
        if not rows[r]:
            continue
        out.append('<div class="eh-row">')
        for f in rows[r]:
            label, kind = labels[f["key"]]
            val = _value(f["key"], dict(ctx, quote=layout.get("quote", "")))
            if kind == "qr":
                body = _qr_svg(ctx.get("qr_text") or f"exam:{ctx['exam'].pk}:{ctx.get('student').pk if ctx.get('student') else 0}")
            else:
                body = f'<b>{html.escape(str(val))}</b>' if val else ""
            cap = "" if kind == "qr" else f'<span class="eh-label">{html.escape(label)}:</span> '
            out.append(f'<div class="eh-cell" style="text-align:{f["align"]}">{cap}{body}</div>')
        out.append("</div>")
    # جمله‌ی قصار و توضیحات، هر کدام در ردیف جدا، زیر سربرگ و وسط‌چین، با خط بریده‌ی پررنگ بین آن‌ها
    for f in sorted(final, key=lambda x: 0 if labels[x["key"]][1] == "quote" else 1):
        label, kind = labels[f["key"]]
        val = _value(f["key"], dict(ctx, quote=layout.get("quote", "")))
        text = html.escape(str(val)).replace("\n", "<br>")
        cap = f'<span class="eh-label">{html.escape(label)}:</span> ' if kind == "description" else ""
        out.append(f'<div class="eh-row eh-final eh-{kind}"><div class="eh-cell eh-center">{cap}{text}</div></div>')
    out.append("</div>")
    return "".join(out)
