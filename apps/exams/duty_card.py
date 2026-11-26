"""کارت ورود به جلسه‌ی مراقبین: همانند کارت دانش‌آموز، با نام، کد ملی، عکس، روزهای مراقبت (شمسی) و امتحان‌های همان روزها."""
import html

from django.core import signing
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.jalali import system_date
from apps.core.models import audit

from .duty_views import _can_manage
from .entry_card_v2 import _photo
from .models import ExamDuty, ExamSchedule, ExamScheduleEntry
from .schedule_views import fa_day


class DutyCardLinkView(APIView):
    """GET ?schedule=ID&teacher=ID — پیوند کوتاه‌مدت کارت مراقب (تب جدید؛ PDF با Ctrl+P)."""

    def get(self, request):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=request.query_params.get("schedule") or 0)
        teacher = get_object_or_404(User, pk=request.query_params.get("teacher") or 0, role="teacher")
        mine = request.user.pk == teacher.pk
        if not (mine or _can_manage(request.user, sch.school)):
            raise PermissionDenied("کارت مراقب را خود مراقب یا مدیر و معاون آموزشی می‌بیند.")
        token = signing.dumps({"s": sch.pk, "t": teacher.pk}, salt="duty-card")
        audit(request, "exam.duty.card", f"schedule:{sch.pk} teacher:{teacher.pk}")
        return Response({"url": f"/api/exam-duty-card/{token}/"})


def _card_html(sch, teacher):
    e = html.escape
    duties = ExamDuty.objects.filter(schedule=sch, teacher=teacher).order_by("date")
    name = e(teacher.get_full_name() or teacher.username)
    photo = _photo(teacher) or ""
    img = (f"<img src='{photo}' style='width:26mm;height:32mm;object-fit:cover;border:1px solid #000'>" if photo
           else "<div style='width:26mm;height:32mm;border:1px solid #000;font-size:9px'>بدون عکس</div>")
    blocks = []
    for d in duties:
        exams = ExamScheduleEntry.objects.filter(schedule=sch, date=d.date).select_related("classroom__grade", "subject")
        lines = "".join(f"<tr><td>{e(x.classroom.grade.name)} — {e(x.classroom.name)}</td><td>{e(x.subject.name)}</td>"
                        f"<td>{x.start_time.strftime('%H:%M')}</td><td>{e(x.venue_name or '')}</td></tr>" for x in exams)
        blocks.append(f"<div class='day'><b>{e(system_date(d.date))} — {e(fa_day(d.date))}</b>"
                      + (f"<table><tr><th>کلاس</th><th>درس</th><th>ساعت</th><th>محل</th></tr>{lines}</table>" if lines else "")
                      + ("<div>معلم درس همین روز است</div>" if d.by_subject else "")
                      + (f"<div><b>توضیحات:</b> {e(d.note)}</div>" if d.note else "") + "</div>")
    from apps.accounts.models import User as _U
    from .duty import teacher_hours as _th
    hrs = _th(sch.school).get(teacher.pk, 0)
    summary = (f"<div style='margin:4px 0'><b>تعداد روزهای مراقبت:</b> {duties.count()} &nbsp; "
               f"<b>جمع ساعات مراقبت:</b> {hrs}</div>") if duties.exists() else ""
    days = summary + ("".join(blocks) or "<p>روز مراقبتی برای شما ثبت نشده است.</p>")
    notes = (f"<div class='notes'><b>تذکرات مهم مراقبت:</b><br>{e(sch.duty_notes).replace(chr(10), '<br>')}</div>" if sch.duty_notes.strip() else "")
    css = """
@font-face{font-family:"VazirDC";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirDC";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:9mm}
html,body{font-family:"VazirDC",Tahoma,sans-serif;color:#000;font-size:11px;margin:0;background:#fff}
.card{border:2px solid #0f5c4d;border-radius:8px;padding:8px}
.ttl{text-align:center;font-weight:800;font-size:13px;border-bottom:1px solid #0f5c4d;padding-bottom:4px;margin-bottom:6px;color:#0f5c4d}
.row{display:flex;gap:10px}.info{flex:1;line-height:2}
.day{background:#eef6f3;border:1px solid #9fc7bd;border-radius:6px;padding:4px 6px;margin:4px 0}
table{width:100%;border-collapse:collapse;margin-top:3px}th,td{border:1px solid #9fc7bd;padding:2px 4px;text-align:center;font-size:10px}
.notes{border:1px solid #c0392b;border-radius:6px;padding:5px;margin-top:6px;background:#fff5f5}
"""
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>کارت مراقب</title><style>{css}</style></head><body>"
            f"<div class='card'><div class='ttl'>کارت ورود به جلسه‌ی مراقبت از امتحانات — {e(sch.title)}<br>"
            f"<span style='font-weight:400'>{e(sch.school.name)}</span></div>"
            f"<div class='row'>{img}<div class='info'><div><b>نام مراقب:</b> {name}</div>"
            f"<div><b>کد ملی:</b> {e(teacher.national_id or '')}</div>"
            f"<div><b>نقش:</b> مراقب امتحان</div></div></div>"
            f"<div style='margin-top:6px'>{days}</div>{notes}"
            f"<div style='margin-top:8px;font-size:10px'>امضای مراقب: ____________ &nbsp; مهر و امضای مدیر مدرسه: ____________</div></div></body></html>")


class DutyCardPublicView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        try:
            p = signing.loads(token, salt="duty-card", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از برنامه‌ی مراقبت چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=p["s"])
        teacher = get_object_or_404(User, pk=p["t"])
        resp = HttpResponse(_card_html(sch, teacher), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp
