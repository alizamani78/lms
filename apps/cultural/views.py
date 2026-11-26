from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, ParentLink, User
from apps.core.models import audit
from apps.core.scope import org_in_scope

from .models import Competition, CompetitionParticipant, CompetitionResult, Trip, TripParticipant

MANAGERS = ("school_admin", "school_deputy_culture")       # the principal and the cultural deputy manage these


def _manager(user, school):
    if user.role not in MANAGERS or not org_in_scope(user, school):
        raise PermissionDenied("این بخش فقط برای مدیر مدرسه و معاون پرورشی است.")


def _school_from(request, data=True):
    sid = (request.data.get("school") if data else request.query_params.get("school")) or ""
    if not str(sid).isdigit():
        raise serializers.ValidationError({"school": "مدرسه را انتخاب کنید."})
    return get_object_or_404(OrgUnit, pk=int(sid), kind="school")


def _student_in_school(student, school):
    if student.role != User.Role.STUDENT or student.org_id != school.pk:
        raise serializers.ValidationError({"student": "دانش‌آموز این مدرسه نیست."})


# ------------------------------------------------------------------ competitions and festivals
def _competition_dict(c, with_results=True):
    out = {"id": c.pk, "kind": c.kind, "kind_label": c.get_kind_display(), "title": c.title, "date": c.date,
           "description": c.description, "is_published": c.is_published}
    if with_results:
        out["participants"] = [{"student": x.student_id, "name": x.student.get_full_name() or x.student.username,
                                "consent": x.consent, "consent_label": x.get_consent_display(), "guardian_name": x.guardian_name}
                               for x in c.participants.select_related("student")]
        out["results"] = [{"rank": r.rank, "name": (r.student.get_full_name() or r.student.username) if r.student_id else r.name,
                           "student": r.student_id, "score": float(r.score) if r.score is not None else None}
                          for r in c.results.select_related("student").order_by("rank")]
    return out


class CompetitionsView(APIView):
    def get(self, request):
        u = request.user
        school = _school_from(request, data=False) if request.query_params.get("school") else None
        if school is None:
            if u.role == User.Role.STUDENT and u.org_id:
                school = u.org
            elif u.org_id and u.role in User.SCHOOL_ROLES:
                school = u.org
            else:
                raise serializers.ValidationError({"school": "مدرسه را انتخاب کنید."})
        if not org_in_scope(u, school) and not (u.role == User.Role.STUDENT and u.org_id == school.pk):
            raise PermissionDenied()
        qs = Competition.objects.filter(school=school).prefetch_related("results__student", "participants__student")
        if u.role in (User.Role.STUDENT, User.Role.PARENT):
            qs = qs.filter(is_published=True)
        return Response([_competition_dict(c) for c in qs[:300]])

    def post(self, request):
        school = _school_from(request)
        _manager(request.user, school)
        title = str(request.data.get("title", "")).strip()[:200]
        if not title:
            raise serializers.ValidationError({"title": "عنوان لازم است."})
        try:
            day = datetime.fromisoformat(str(request.data.get("date"))).date()
        except (TypeError, ValueError):
            raise serializers.ValidationError({"date": "تاریخ نامعتبر است."})
        kind = request.data.get("kind") if request.data.get("kind") in ("competition", "festival") else "competition"
        c = Competition.objects.create(school=school, kind=kind, title=title, date=day,
                                       description=str(request.data.get("description", ""))[:5000], created_by=request.user)
        audit(request, "cultural.competition", f"competition:{c.pk}")
        return Response(_competition_dict(c), status=201)


class CompetitionDetailView(APIView):
    def patch(self, request, pk):
        c = get_object_or_404(Competition, pk=pk)
        _manager(request.user, c.school)
        for k in ("title", "description"):
            if k in request.data:
                setattr(c, k, str(request.data[k])[:5000 if k == "description" else 200])
        if "is_published" in request.data:
            c.is_published = bool(request.data["is_published"])
        c.save()
        return Response(_competition_dict(c))

    def delete(self, request, pk):
        c = get_object_or_404(Competition, pk=pk)
        _manager(request.user, c.school)
        c.delete()
        return Response(status=204)


class CompetitionResultsView(APIView):
    """PUT — the top five (رتبه ۱ تا ۵) with the score typed by hand. Replaces the previous list."""

    def put(self, request, pk):
        c = get_object_or_404(Competition, pk=pk)
        _manager(request.user, c.school)
        rows = request.data.get("results") or []
        if len(rows) > 5:
            raise serializers.ValidationError({"results": "حداکثر پنج نفر برتر (رتبه ۱ تا ۵)."})
        seen, clean = set(), []
        for row in rows:
            try:
                rank = int(row.get("rank"))
            except (TypeError, ValueError):
                raise serializers.ValidationError({"results": "رتبه نامعتبر است."})
            if not 1 <= rank <= 5 or rank in seen:
                raise serializers.ValidationError({"results": "هر رتبه از ۱ تا ۵ فقط یک بار."})
            seen.add(rank)
            score = None
            if str(row.get("score", "")).strip() != "":
                try:
                    score = Decimal(str(row.get("score")))
                except InvalidOperation:
                    raise serializers.ValidationError({"results": "امتیاز نامعتبر است."})
            student = None
            if row.get("student"):
                student = get_object_or_404(User, pk=row["student"])
                _student_in_school(student, c.school)
            clean.append(CompetitionResult(competition=c, rank=rank, student=student,
                                           name=str(row.get("name", ""))[:200] if student is None else "", score=score))
        c.results.all().delete()
        CompetitionResult.objects.bulk_create(clean)
        audit(request, "cultural.results", f"competition:{c.pk}")
        return Response(_competition_dict(c))


# ------------------------------------------------------------------ trips and consent forms
def _trip_dict(t, with_participants=True):
    out = {"id": t.pk, "title": t.title, "destination": t.destination, "start_date": t.start_date, "end_date": t.end_date,
           "description": t.description}
    if with_participants:
        out["participants"] = [{"id": p.pk, "student": p.student_id, "name": p.student.get_full_name() or p.student.username,
                                "consent": p.consent, "consent_label": p.get_consent_display(), "guardian_name": p.guardian_name}
                               for p in t.participants.select_related("student").order_by("student__last_name", "student__username")]
    return out


def _parse_day(v):
    """«۲۰۲۶-۱۱-۲۵» یا تاریخ ISO؛ خالی یعنی بدون مهلت."""
    import datetime as dt
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _parse_dt(v):
    """«۱۴۰۵/۰۸/۲۳ ۰۷:۰۰» یا ISO؛ خالی یعنی بدون زمان."""
    if not v:
        return None
    try:
        from django.utils.dateparse import parse_datetime
        return parse_datetime(str(v)) or None
    except (TypeError, ValueError):
        return None


class TripsView(APIView):
    def get(self, request):
        u = request.user
        school = _school_from(request, data=False) if request.query_params.get("school") else None
        if school is None and u.org_id and u.role in User.SCHOOL_ROLES:
            school = u.org
        if school is None:
            raise serializers.ValidationError({"school": "مدرسه را انتخاب کنید."})
        if not org_in_scope(u, school):
            raise PermissionDenied()
        qs = Trip.objects.filter(school=school).prefetch_related("participants__student")
        return Response([_trip_dict(t) for t in qs[:200]])

    def post(self, request):
        school = _school_from(request)
        _manager(request.user, school)
        title = str(request.data.get("title", "")).strip()[:200]
        if not title:
            raise serializers.ValidationError({"title": "عنوان اردو لازم است."})
        try:
            start = datetime.fromisoformat(str(request.data.get("start_date"))).date()
            end = datetime.fromisoformat(str(request.data.get("end_date"))).date()
        except (TypeError, ValueError):
            raise serializers.ValidationError({"start_date": "تاریخ‌ها نامعتبر است."})
        if end < start:
            raise serializers.ValidationError({"end_date": "پایان اردو نمی‌تواند قبل از شروع باشد."})
        t = Trip.objects.create(school=school, title=title, destination=str(request.data.get("destination", ""))[:200],
                                start_date=start, end_date=end, description=str(request.data.get("description", ""))[:5000],
                                trip_type=str(request.data.get("trip_type") or "excursion"),
                                departure_place=str(request.data.get("departure_place", ""))[:200],
                                duration_days=int(request.data["duration_days"]) if str(request.data.get("duration_days") or "").isdigit() else None,
                                consent_deadline=_parse_day(request.data.get("consent_deadline")),
                                departure_at=_parse_dt(request.data.get("departure_at")),
                                created_by=request.user)
        audit(request, "cultural.trip", f"trip:{t.pk}")
        return Response(_trip_dict(t), status=201)


class TripParticipantsView(APIView):
    """PUT [{student, consent: pending|accepted|declined, guardian_name}] — who goes and whether the guardian consented."""

    def put(self, request, pk):
        t = get_object_or_404(Trip, pk=pk)
        _manager(request.user, t.school)
        for row in request.data.get("participants") or []:
            student = get_object_or_404(User, pk=row.get("student") or 0)
            _student_in_school(student, t.school)
            consent = row.get("consent") if row.get("consent") in ("pending", "accepted", "declined") else "pending"
            p, _ = TripParticipant.objects.get_or_create(trip=t, student=student)
            p.consent = consent
            p.guardian_name = str(row.get("guardian_name", ""))[:150]
            p.consent_at = timezone.now() if consent != "pending" else None
            p.save()
        audit(request, "cultural.trip.participants", f"trip:{t.pk}")
        return Response(_trip_dict(t))


def _principal_name(school):
    """نام مدیر مدرسه (برای امضای رضایت‌نامه)."""
    from apps.accounts.models import SchoolMembership
    m = SchoolMembership.objects.filter(school=school, user__role="school_admin").select_related("user").first()
    return (m.user.get_full_name() or m.user.username) if m else ""


def _class_name(student):
    from apps.academics.models import ClassRoom
    c = ClassRoom.objects.filter(students=student, academic_year__is_current=True).select_related("grade").first()
    return c.name if c else ""


def _grade_name(student):
    from apps.academics.models import ClassRoom
    c = ClassRoom.objects.filter(students=student, academic_year__is_current=True).select_related("grade").first()
    return c.grade.name if c and c.grade_id else ""


class ConsentPrintLinkView(APIView):
    """GET /api/cultural/trips/<trip>/consent/<student>/print-link/ — پیوند کوتاه‌مدت چاپ رضایت‌نامه (فرم رسمی)."""

    def get(self, request, pk, student_id):
        from django.core import signing
        t = get_object_or_404(Trip, pk=pk)
        student = get_object_or_404(User, pk=student_id, role=User.Role.STUDENT)
        u = request.user
        allowed = (u.role in MANAGERS and org_in_scope(u, t.school)) or (u.role == User.Role.STUDENT and u.pk == student.pk) \
            or (u.role == User.Role.PARENT and ParentLink.objects.filter(parent=u, student=student, is_verified=True).exists())
        if not allowed or student.org_id != t.school_id:
            raise PermissionDenied()
        size = request.query_params.get("size") if request.query_params.get("size") in ("a5", "a4l") else "a5"
        token = signing.dumps({"t": t.pk, "s": student.pk, "z": size}, salt="trip-consent")
        audit(request, "cultural.consent.print", f"trip:{t.pk} student:{student.pk}")
        return Response({"url": f"/api/cultural/consent-print/{token}/"})


class ConsentPublicPrintView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from django.core import signing
        from django.http import HttpResponse
        from .consent_print import render
        try:
            p = signing.loads(token, salt="trip-consent", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی اردو چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        t = get_object_or_404(Trip, pk=p["t"])
        student = get_object_or_404(User, pk=p["s"])
        part = TripParticipant.objects.filter(trip=t, student=student).first()
        d = {"trip": t.title, "destination": t.destination, "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(),
             "trip_type_key": t.trip_type, "departure_at": t.departure_at.isoformat() if t.departure_at else "",
             "departure_place": t.departure_place, "duration_days": t.duration_days,
             "student": student.get_full_name() or student.username, "class_name": _class_name(student), "grade_name": _grade_name(student),
             "guardian_name": part.guardian_name if part else "", "principal": _principal_name(t.school), "phone": student.mobile or "",
             "consent_deadline": t.consent_deadline.isoformat() if t.consent_deadline else ""}
        resp = HttpResponse(render(d, part, size=p.get("z", "a5")), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp


class ConsentFormView(APIView):
    """GET /api/cultural/trips/<trip>/consent/<student>/ — the data of the consent form (رضایت‌نامه) to print."""

    def get(self, request, pk, student_id):
        t = get_object_or_404(Trip, pk=pk)
        student = get_object_or_404(User, pk=student_id, role=User.Role.STUDENT)
        u = request.user
        allowed = (u.role in MANAGERS and org_in_scope(u, t.school)) or (u.role == User.Role.STUDENT and u.pk == student.pk) \
            or (u.role == User.Role.PARENT and ParentLink.objects.filter(parent=u, student=student, is_verified=True).exists())
        if not allowed or student.org_id != t.school_id:
            raise PermissionDenied()
        p = TripParticipant.objects.filter(trip=t, student=student).first()
        return Response({"school": t.school.name, "trip": t.title, "destination": t.destination,
                         "start_date": t.start_date, "end_date": t.end_date, "description": t.description,
                         "student": student.get_full_name() or student.username, "student_code": student.student_code or "",
                         "consent": p.get_consent_display() if p else "—", "guardian_name": p.guardian_name if p else "",
                         "trip_type": t.get_trip_type_display(), "trip_type_key": t.trip_type,
                         "departure_at": t.departure_at.isoformat() if t.departure_at else "",
                         "departure_place": t.departure_place, "duration_days": t.duration_days,
                         "class_name": _class_name(student), "grade_name": _grade_name(student),
                         "consent_deadline": t.consent_deadline.isoformat() if t.consent_deadline else "",
                         "principal": _principal_name(t.school)})


class CompetitionParticipantsView(APIView):
    """PUT [{student, consent, guardian_name}] — who takes part in the competition or festival, and the guardian's consent."""

    def put(self, request, pk):
        c = get_object_or_404(Competition, pk=pk)
        _manager(request.user, c.school)
        for row in request.data.get("participants") or []:
            student = get_object_or_404(User, pk=row.get("student") or 0)
            _student_in_school(student, c.school)
            consent = row.get("consent") if row.get("consent") in ("pending", "accepted", "declined") else "pending"
            p, _ = CompetitionParticipant.objects.get_or_create(competition=c, student=student)
            p.consent = consent
            p.guardian_name = str(row.get("guardian_name", ""))[:150]
            p.consent_at = timezone.now() if consent != "pending" else None
            p.save()
        audit(request, "cultural.competition.participants", f"competition:{c.pk}")
        return Response(_competition_dict(c))


class CompetitionConsentFormView(APIView):
    """GET /api/cultural/competitions/<id>/consent/<student>/ — the consent form data for a festival (printable)."""

    def get(self, request, pk, student_id):
        c = get_object_or_404(Competition, pk=pk)
        student = get_object_or_404(User, pk=student_id, role=User.Role.STUDENT)
        u = request.user
        allowed = (u.role in MANAGERS and org_in_scope(u, c.school)) or (u.role == User.Role.STUDENT and u.pk == student.pk) \
            or (u.role == User.Role.PARENT and ParentLink.objects.filter(parent=u, student=student, is_verified=True).exists())
        if not allowed or student.org_id != c.school_id:
            raise PermissionDenied()
        p = CompetitionParticipant.objects.filter(competition=c, student=student).first()
        return Response({"school": c.school.name, "trip": c.title, "destination": c.get_kind_display(),
                         "start_date": c.date, "end_date": c.date, "description": c.description,
                         "student": student.get_full_name() or student.username, "student_code": student.student_code or "",
                         "consent": p.get_consent_display() if p else "—", "guardian_name": p.guardian_name if p else "",
                         "form": "جشنواره"})
