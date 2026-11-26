"""سربرگ برگه‌ی امتحان: خواندن و ذخیره‌ی انتخاب فیلدها و ترتیب آن‌ها، و برگه‌ی چاپی هر دانش‌آموز."""
import html

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.scope import org_in_scope

from . import header as H
from .models import Exam, ExamHeaderLayout, ExamSeat

EDITORS = ("super_admin", "school_admin", "school_deputy_edu")


def _check(user, school, roles=EDITORS):
    if user.role not in roles or not org_in_scope(user, school):
        raise PermissionDenied("سربرگ را مدیر یا معاون آموزشی مدرسه تنظیم می‌کند.")


class HeaderFieldsView(APIView):
    """فهرست فیلدهای قابل انتخاب برای سربرگ."""

    def get(self, request):
        return Response([{"key": k, "label": l, "kind": t} for k, l, t in H.FIELDS])


class HeaderLayoutView(APIView):
    """GET ?school=ID — سربرگ فعلی. PUT {school, fields:[...], quote} — ذخیره‌ی سربرگ."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school, EDITORS + ("school_deputy_exec", "teacher"))
        return Response(H.layout_for(school))

    def put(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request.user, school)
        try:
            layout = H.clean_layout(request.data)
        except (ValueError, TypeError) as e:
            raise serializers.ValidationError({"layout": str(e)})
        if not any(f["enabled"] for f in layout["fields"]):
            raise serializers.ValidationError({"fields": "حداقل یک فیلد را برای سربرگ انتخاب کنید."})
        ExamHeaderLayout.objects.update_or_create(school=school, defaults={"layout": layout})
        audit(request, "exam.header.save", f"school:{school.pk}")
        return Response(layout)


LETTERS = ["الف", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ", "د", "ذ", "ر", "ز", "ژ", "س", "ش", "ص", "ض", "ط", "ظ"]


def _question_block(q, n):
    """یک سؤال برای چاپ: متن، بارم، گزینه‌ها با حروف (یا خط پاسخ)."""
    from apps.core.htmlclean import clean_html
    out = [f'<div class="q"><div><span class="qn">{n}.</span> {clean_html(q.text or "")} <span>({float(q.points):g} نمره)</span></div>']
    choices = list(q.choices.all())
    if q.kind in ("mcq", "tf", "multi"):
        for i, c in enumerate(choices):
            letter = LETTERS[i] if i < len(LETTERS) else str(i + 1)
            out.append(f'<div class="opt">{letter}) {clean_html(c.text or "")}</div>')
    elif q.kind == "match":
        for c in choices:
            out.append(f'<div class="opt">{clean_html(c.text or "")} ..... {html.escape(c.pair_text or "")}</div>')
    elif q.kind == "drag":
        out.append('<div class="opt">' + " — ".join(clean_html(c.text or "") for c in choices) + "</div>")
    else:
        out.append('<div class="line"></div><div class="line"></div>')
    out.append("</div>")
    return "".join(out)


def _questions_html(exam, with_sections=True):
    """سؤال‌ها برای چاپ. با with_sections، هر بخش با عنوان و متن راهنمای خودش (پاک‌سازی‌شده) چاپ می‌شود."""
    from apps.core.htmlclean import clean_html
    qs = list(exam.questions.prefetch_related("choices").select_related("section").order_by("order", "id"))
    out = []
    n = 0
    if not with_sections:
        for q in qs:
            n += 1
            out.append(_question_block(q, n))
        return "".join(out)
    groups = [(None, [q for q in qs if q.section_id is None])]
    for sec in exam.sections.all():
        groups.append((sec, [q for q in qs if q.section_id == sec.pk]))
    first = True
    for sec, items in groups:
        if not items:
            continue
        if not first:
            out.append('<hr class="sep">')      # خط جداساز بین بخش‌ها
        first = False
        if sec is not None:
            ins = clean_html(sec.instructions or "")
            out.append(f'<div class="sec"><div class="sec-title">{html.escape(sec.title)}</div>'
                       + (f'<div class="sec-ins">{ins}</div>' if ins else "") + "</div>")
        for q in items:
            n += 1
            out.append(_question_block(q, n))
    return "".join(out)


SHEET_CSS = """
@font-face{font-family:"VazirSheet";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirSheet";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{margin:10mm}
html,body{font-family:"VazirSheet",Tahoma,sans-serif;color:#111;font-size:13px;line-height:1.9;margin:0;background:#fff}
.sheet{margin:0 auto}
.exam-header{border:1.5px solid #222;border-radius:8px;padding:8px 12px;margin-bottom:12px;box-sizing:border-box}
.eh-row{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;padding:4px 0;border-bottom:1px dashed #cfcfcf}
.eh-row:last-child{border-bottom:0}
.eh-cell{flex:1 1 0;min-width:110px;font-size:13px}
.eh-label{color:#555;font-size:12px}
.eh-quote,.eh-description{border-top:2.5px dashed #111;margin-top:8px;padding-top:8px}
.eh-quote .eh-cell,.eh-description .eh-cell{text-align:center}
svg{width:64px;height:64px;display:block}
.meta{font-size:13px;margin:4px 0 14px}
.q{border:1px solid #d9d9d9;border-radius:6px;padding:8px 10px;margin:10px 0;page-break-inside:avoid}
.q .qn{font-weight:800}
.opt{margin:3px 0 3px 18px}
.line{border-bottom:1px dotted #777;height:24px;margin:6px 0}
.sec{margin:14px 0 6px;padding:6px 10px;border-right:4px solid #111;page-break-after:avoid}
.sec-title{font-weight:800;font-size:14px}
.sec-ins{font-size:12.5px;color:#333;margin-top:2px}
.exam-desc{border:1px solid #bbb;border-radius:6px;padding:8px 10px;margin:8px 0;font-size:12.5px}
hr.sep{border:0;border-top:2px solid #111;margin:12px 0}
@media print{.q{box-shadow:none}}
"""


def build_sheet_html(exam, student_id=None, with_questions=False, exam_hall="", with_sections=True,
                     with_description=False, with_header=True):
    """برگه‌ی امتحان: سربرگ انتخاب‌شده، بارم کل، و (اختیاری) سؤال‌ها."""
    from apps.accounts.models import User
    from apps.academics.models import ClassRoom
    school = exam.course.org
    student = User.objects.filter(pk=student_id).first() if student_id else None
    candidate = None
    classroom = None
    if student is not None:
        seat = ExamSeat.objects.filter(exam=exam, student=student).first()
        candidate = seat.number if seat else None
        classroom = ClassRoom.objects.filter(school=school, students=student, academic_year__is_current=True) \
            .select_related("grade__level", "grade__branch", "study_field").first()
    layout = H.layout_for(school)
    ctx = {"exam": exam, "school": school, "student": student, "candidate_no": candidate, "classroom": classroom,
           "exam_hall": exam_hall or "", "quote": layout.get("quote", "")}
    head = H.render_header(ctx, layout) if with_header else ""
    # خط جداساز بین سربرگ و توضیحات، و بین توضیحات و سؤال‌ها
    sep_top = '<hr class="sep">' if with_header else ""
    total = sum(float(q.points) for q in exam.questions.all())
    from apps.core.htmlclean import clean_html
    desc = f'<div class="exam-desc">{clean_html(exam.instructions or "")}</div>' if with_description and exam.instructions else ""
    if desc:
        desc = sep_top + desc + '<hr class="sep">'
    body = _questions_html(exam, with_sections) if with_questions else ""
    paper = H.PAPERS.get(layout.get("paper", "A4"), H.PAPERS["A4"])
    width = int(layout.get("width") or 100)
    return (f'<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>برگه‌ی امتحان</title>'
            f'<style>@page{{size:{paper};}} .sheet{{width:{width}%;}}{SHEET_CSS}</style></head><body><div class="sheet">{head}{desc}'
            f'<p class="meta">بارم کل آزمون: <b>{total:g}</b></p>{body}</div></body></html>')


def sheet_response(html_text):
    resp = HttpResponse(html_text, content_type="text/html; charset=utf-8")
    # the printable sheet is a plain document: fonts from this site, inline styles for its layout
    resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data: 'self'; font-src 'self'"
    resp["Cache-Control"] = "no-store"
    return resp


class SheetLinkView(APIView):
    """GET ?student=ID&questions=1&exam_hall=متن — پیوند کوتاه‌مدت برای باز کردن برگه در تب جدید (بدون نیاز به هدر احراز)."""

    def get(self, request, pk):
        from django.core import signing
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=pk)
        _check(request.user, exam.course.org, EDITORS + ("school_deputy_exec", "teacher"))
        qp = request.query_params
        payload = {"e": exam.pk, "s": qp.get("student") or "", "q": qp.get("questions") == "1",
                   "sec": qp.get("sections") != "0", "d": qp.get("description") == "1",
                   "hd": qp.get("header") != "0", "hl": qp.get("exam_hall") or ""}
        token = signing.dumps(payload, salt="exam-sheet")
        audit(request, "exam.sheet.link", f"exam:{exam.pk}")
        return Response({"url": f"/api/sheet/{token}/"})


class SheetPublicView(APIView):
    """GET /api/sheet/<token>/ — برگه‌ی چاپی. فقط با پیوند امضاشده و تازه (۱۵ دقیقه)."""
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from django.core import signing
        try:
            p = signing.loads(token, salt="exam-sheet", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی آزمون باز کنید.", status=403, content_type="text/plain; charset=utf-8")
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=p["e"])
        return sheet_response(build_sheet_html(exam, p.get("s") or None, bool(p.get("q")), p.get("hl") or "",
                                               with_sections=bool(p.get("sec", True)), with_description=bool(p.get("d")),
                                               with_header=bool(p.get("hd", True))))


class SheetView(APIView):
    """GET ?student=ID&exam_hall=متن — برگه‌ی چاپی امتحان با سربرگ انتخاب‌شده (HTML، برای چاپ مرورگر یا PDF)."""

    def get(self, request, pk):
        exam = get_object_or_404(Exam.objects.select_related("course__org", "course__teacher", "course__classroom"), pk=pk)
        school = exam.course.org
        _check(request.user, school, EDITORS + ("school_deputy_exec", "teacher"))
        from apps.accounts.models import User
        student = None
        candidate = None
        if request.query_params.get("student"):
            student = get_object_or_404(User, pk=request.query_params["student"])
            seat = ExamSeat.objects.filter(exam=exam, student=student).first()
            candidate = seat.number if seat else None
        classroom = None
        if student is not None:
            from apps.academics.models import ClassRoom
            classroom = ClassRoom.objects.filter(school=school, students=student, academic_year__is_current=True) \
                .select_related("grade__level", "grade__branch", "study_field").first()
        layout = H.layout_for(school)
        ctx = {"exam": exam, "school": school, "student": student, "candidate_no": candidate, "classroom": classroom,
               "exam_hall": request.query_params.get("exam_hall") or "", "quote": layout.get("quote", "")}
        head = H.render_header(ctx, layout)
        total = sum(float(q.points) for q in exam.questions.all())
        body = _questions_html(exam) if request.query_params.get("questions") == "1" else ""
        page = f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>برگه‌ی امتحان</title>
<style>body{{font-family:Tahoma,sans-serif;margin:18px;color:#111}} .exam-header{{border:1.5px solid #333;border-radius:8px;padding:8px 12px;margin-bottom:14px}}
.eh-row{{display:flex;gap:14px;align-items:center;margin:4px 0;flex-wrap:wrap}} .eh-cell{{flex:1 1 0;min-width:120px;font-size:13px}}
.eh-label{{color:#555}} .eh-final{{justify-content:center;border-top:1px solid #999;margin-top:6px;padding-top:6px}} .eh-center{{text-align:center;flex:1 1 100%}}
.q{{margin:12px 0;page-break-inside:avoid}} .q .qn{{font-weight:bold}} .opt{{margin:2px 0 2px 18px}} .line{{border-bottom:1px dotted #777;height:22px;margin:6px 0}} svg{{width:70px;height:70px}} .meta{{font-size:13px;margin:4px 0 12px}} @media print{{body{{margin:8mm}}}}</style></head>
<body>{head}<p class="meta">بارم کل آزمون: <b>{total:g}</b></p>{body}</body></html>"""
        audit(request, "exam.sheet", f"exam:{exam.pk} student:{student.pk if student else 0}")
        resp = HttpResponse(page, content_type="text/html; charset=utf-8")
        # the printable sheet is a plain document: its own small policy (inline style, the QR image inside the page)
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data: 'self'; font-src 'self'"
        return resp


class EntryLinkView(APIView):
    """GET ?paper=A4&landscape=0&per_page=2&staff=1&room=… — پیوند کوتاه‌مدت کارت‌های ورود (در تب جدید باز می‌شود)."""

    def get(self, request, pk):
        from django.core import signing
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=pk) if pk else None
        if exam is not None:
            school = exam.course.org
        else:
            from apps.accounts.models import OrgUnit
            school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school, EDITORS + ("school_deputy_exec",))
        payload = {"e": exam.pk if exam is not None else None, "sch": school.pk, "g": request.query_params.get("grade") or None,
                   "p": request.query_params.get("paper") or "A4",
                   "l": request.query_params.get("landscape") == "1", "n": request.query_params.get("per_page") or "2",
                   "f": request.query_params.get("staff") != "0", "r": request.query_params.get("room") or "",
                   "z": request.query_params.get("photo_size") or "normal",
                   "i": request.query_params.get("ident") or "both",
                   "s": [int(x) for x in (request.query_params.get("students") or "").split(",") if x.isdigit()]}
        token = signing.dumps(payload, salt="entry-cards")
        audit(request, "exam.entry.link", f"exam:{exam.pk if exam is not None else 0} school:{school.pk}")
        return Response({"url": f"/api/entry-cards/{token}/"})


class EntryPublicView(APIView):
    """GET /api/entry-cards/<token>/ — کارت‌های ورود چاپی (فقط با پیوند امضاشده‌ی تازه)."""
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from django.core import signing
        from .entry_card_v2 import build_entry_cards
        try:
            p = signing.loads(token, salt="entry-cards", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی آزمون باز کنید.", status=403, content_type="text/plain; charset=utf-8")
        exam = get_object_or_404(Exam.objects.select_related("course__org", "course__teacher"), pk=p["e"]) if p.get("e") else None
        from apps.accounts.models import OrgUnit
        school = exam.course.org if exam is not None else get_object_or_404(OrgUnit, pk=p.get("sch") or 0)
        doc = build_entry_cards(exam, paper=p.get("p", "A4"), landscape=bool(p.get("l")), per_page=int(p.get("n") or 2),
                                staff=bool(p.get("f", True)), room=p.get("r", ""), photo_size=p.get("z", "normal"),
                                students=p.get("s") or None, school=school, grade=p.get("g") or None,
                                ident=p.get("i") or "both")
        if doc is None:
            return HttpResponse("برای این آزمون هنوز شماره‌ی داوطلبی ساخته نشده است.", status=422, content_type="text/plain; charset=utf-8")
        return sheet_response(doc)
