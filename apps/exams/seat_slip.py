"""برگه‌ی روی صندلی: برای هر داوطلب، شماره‌ی صندلی و شماره‌ی داوطلبی، کد دانش‌آموزی، نام و نام خانوادگی و عکس؛
همان شماره‌ی صندلی و کد دانش‌آموزی که روی کارت ورود به جلسه است."""
import html

from django.core import signing
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated  # noqa: F401
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import audit

from .entry_card_v2 import _photo
from .header_views import _check
from .models import Exam, ExamSeat

EDITORS = ("super_admin", "school_admin", "school_deputy_edu")


def _slip_html(exam, seats):
    e = html.escape
    title = exam.title if exam is not None else "همه‌ی آزمون‌های مدرسه"
    blocks = []
    for seat in seats:
        st = seat.student
        photo = _photo(st) or ""
        img = (f"<img src='{photo}' style='width:24mm;height:30mm;object-fit:cover;border:1px solid #000'>"
               if photo else "<div style='width:24mm;height:30mm;border:1px solid #000;font-size:9px'>بدون عکس</div>")
        blocks.append(
            f"<div class='slip'><div class='seat'>صندلی <b>{e(str(seat.room or ''))}</b> — شماره‌ی داوطلبی <b>{seat.number}</b></div>"
            f"<div class='row'>{img}<div class='info'>"
            f"<div><b>نام و نام خانوادگی:</b> {e((st.get_full_name() or st.username))}</div>"
            f"<div><b>کد دانش‌آموزی:</b> {e(st.student_code or '')}</div>"
            f"<div><b>آزمون:</b> {e(title)}</div>"
            f"<div class='sign'>امضای داوطلب: ____________</div></div></div></div>")
    body = "".join(blocks) or "<p>داوطلبی برای این آزمون ثبت نشده است.</p>"
    css = """
@font-face{font-family:"VazirSlip";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirSlip";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:8mm}
html,body{font-family:"VazirSlip",Tahoma,sans-serif;color:#000;font-size:11px;margin:0;background:#fff}
.slip{border:1.5px solid #000;padding:6px;margin:0 0 8px 0;page-break-inside:avoid}
.seat{font-weight:800;border-bottom:1px solid #000;padding-bottom:4px;margin-bottom:6px;font-size:13px}
.row{display:flex;gap:10px;align-items:flex-start}.info{flex:1;line-height:2}.sign{margin-top:8px}
"""
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>برگه‌ی صندلی</title><style>{css}</style></head>"
            f"<body><div style='text-align:center;font-weight:800;margin-bottom:6px'>برگه‌ی روی صندلی — {e(title)}</div>{body}</body></html>")


class SeatSlipLinkView(APIView):
    """GET /api/exams/<pk>/seat-slip/print-link/?students=1,2,… — پیوند چاپ برگه‌ی روی صندلی."""

    def get(self, request, pk):
        from apps.accounts.models import OrgUnit
        if pk:
            exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=pk)
            school = exam.course.org
        else:
            exam = None
            school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school, EDITORS + ("school_deputy_exec",))
        ids = [int(x) for x in (request.query_params.get("students") or "").split(",") if x.isdigit()]
        token = signing.dumps({"e": exam.pk if exam is not None else None, "s": ids}, salt="seat-slip")
        audit(request, "exam.seat_slip.print", f"exam:{pk or 0}")
        return Response({"url": f"/api/entry-seat-slip/{token}/"})


class SeatSlipPublicView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        try:
            p = signing.loads(token, salt="seat-slip", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی کارت ورود چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=p["e"]) if p.get("e") else None
        qs = ExamSeat.objects.select_related("student", "exam__course__org")
        if exam is not None:
            qs = qs.filter(exam=exam)
        if p.get("s"):
            qs = qs.filter(student_id__in=p["s"])
        # one slip per candidate: the candidate keeps the same seat and number for all of the school's exams
        first = {}
        for seat in qs.order_by("number", "id"):
            first.setdefault(seat.student_id, seat)
        resp = HttpResponse(_slip_html(exam, list(first.values())), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp
