from datetime import datetime

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, ParentLink, SchoolMembership, User
from apps.core.htmlclean import clean_html
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.sms.notify import enqueue_user

from .models import Decision, Invitation, Meeting

MANAGERS = ("school_admin", "school_deputy_edu", "school_deputy_exec")   # principal and the two deputies
STAFF_IN_SCHOOL = ("school_admin", "school_deputy_edu", "school_deputy_exec", "teacher")


def _manager(user, school):
    if user.role not in MANAGERS or not org_in_scope(user, school):
        raise PermissionDenied("جلسات را مدیر مدرسه، معاون آموزشی یا معاون اجرایی مدیریت می‌کند.")


def _school(request, data=True):
    sid = (request.data.get("school") if data else request.query_params.get("school")) or ""
    if not str(sid).isdigit():
        raise serializers.ValidationError({"school": "مدرسه را انتخاب کنید."})
    return get_object_or_404(OrgUnit, pk=int(sid), kind="school")


def _name(u):
    return u.get_full_name() or u.username


def _candidates(school, kind):
    """Who may be invited: staff of the school (teachers and managers) and, for parents' meetings and school councils,
    the parents linked to the school's students."""
    staff = User.objects.filter(Q(school_memberships__school=school) | Q(org=school), role__in=STAFF_IN_SCHOOL, is_active=True).distinct()
    out = list(staff)
    if kind in ("parents_meeting", "school_council"):
        parents = User.objects.filter(role="parent", child_links__is_verified=True,
                                      child_links__student__org=school, is_active=True).distinct()
        out += list(parents)
    return out


class CandidatesView(APIView):
    def get(self, request):
        school = _school(request, data=False)
        if request.user.role not in MANAGERS or not org_in_scope(request.user, school):
            raise PermissionDenied()
        kind = request.query_params.get("kind") or "teachers_council"
        return Response([{"id": u.pk, "label": f"{_name(u)} ({dict(User.Role.choices).get(u.role, u.role)})", "role": u.role}
                         for u in sorted(_candidates(school, kind), key=lambda x: (x.role, x.last_name, x.username))])


def _meeting_dict(m, full=True):
    out = {"id": m.pk, "kind": m.kind, "kind_label": m.get_kind_display(), "title": m.title,
           "starts_at": m.starts_at, "ends_at": m.ends_at, "location": m.location, "agenda": m.agenda}
    if full:
        out["minutes"] = m.minutes
        out["invitations"] = [{"id": i.pk, "invitee": i.invitee_id, "name": _name(i.invitee), "role": i.invitee.role,
                               "sent_at": i.sent_at, "attended": i.attended} for i in m.invitations.select_related("invitee")]
        out["decisions"] = [{"id": d.pk, "text": d.text, "follow_up": d.follow_up_id,
                             "follow_up_name": _name(d.follow_up) if d.follow_up_id else "", "due_date": d.due_date, "done": d.done}
                            for d in m.decisions.select_related("follow_up")]
    return out


class MeetingsView(APIView):
    def get(self, request):
        school = _school(request, data=False)
        if request.user.role not in MANAGERS or not org_in_scope(request.user, school):
            raise PermissionDenied()
        return Response([_meeting_dict(m, full=False) for m in Meeting.objects.filter(school=school)[:300]])

    def post(self, request):
        school = _school(request)
        _manager(request.user, school)
        d = request.data
        title = str(d.get("title", "")).strip()[:200]
        if not title:
            raise serializers.ValidationError({"title": "عنوان جلسه لازم است."})
        try:
            start = datetime.fromisoformat(str(d.get("starts_at")))
            end = datetime.fromisoformat(str(d.get("ends_at")))
        except (TypeError, ValueError):
            raise serializers.ValidationError({"starts_at": "زمان شروع و پایان را انتخاب کنید."})
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        if timezone.is_naive(end):
            end = timezone.make_aware(end)
        if end <= start:
            raise serializers.ValidationError({"ends_at": "پایان جلسه باید بعد از شروع آن باشد."})
        kind = d.get("kind") if d.get("kind") in dict(Meeting.KIND) else "teachers_council"
        m = Meeting.objects.create(school=school, kind=kind, title=title, starts_at=start, ends_at=end,
                                   location=str(d.get("location", ""))[:200], agenda=str(d.get("agenda", ""))[:5000],
                                   created_by=request.user)
        audit(request, "meeting.create", f"meeting:{m.pk}")
        return Response(_meeting_dict(m), status=201)


class MeetingDetailView(APIView):
    def get(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        if request.user.role not in MANAGERS:
            if not m.invitations.filter(invitee=request.user).exists() or not org_in_scope(request.user, m.school):
                raise PermissionDenied()
        return Response(_meeting_dict(m))

    def patch(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        _manager(request.user, m.school)
        for k in ("title", "location", "agenda"):
            if k in request.data:
                setattr(m, k, str(request.data[k])[:5000 if k == "agenda" else 200])
        if "minutes" in request.data:
            m.minutes = clean_html(str(request.data["minutes"]))[0]        # صورت‌جلسه: cleaned like all rich text
        if "kind" in request.data and request.data["kind"] in dict(Meeting.KIND):
            m.kind = request.data["kind"]
        for k in ("starts_at", "ends_at"):
            if k in request.data:
                try:
                    v = datetime.fromisoformat(str(request.data[k]))
                except ValueError:
                    raise serializers.ValidationError({k: "زمان نامعتبر است."})
                setattr(m, k, timezone.make_aware(v) if timezone.is_naive(v) else v)
        m.full_clean()
        m.save()
        audit(request, "meeting.update", f"meeting:{m.pk}")
        return Response(_meeting_dict(m))

    def delete(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        _manager(request.user, m.school)
        m.delete()
        return Response(status=204)


class InvitationsView(APIView):
    """PUT {invitees:[user ids]} — the list of invited people. New invitees get the invitation: the SMS is queued
    for every one who has a mobile number (through the school's panel). Removing an invitee removes the invitation."""

    def put(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        _manager(request.user, m.school)
        allowed = {u.pk: u for u in _candidates(m.school, m.kind)}
        ids = []
        for raw in request.data.get("invitees") or []:
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"invitees": "فهرست نامعتبر است."})
            if uid not in allowed:
                raise serializers.ValidationError({"invitees": "یکی از افراد دعوت‌شده این مدرسه یا این نوع جلسه نیست."})
            ids.append(uid)
        m.invitations.exclude(invitee_id__in=ids).delete()
        sms = 0
        when = timezone.localtime(m.starts_at).strftime("%Y-%m-%d %H:%M")
        for uid in ids:
            inv, created = Invitation.objects.get_or_create(meeting=m, invitee=allowed[uid])
            if created or inv.sent_at is None:
                inv.sent_at = timezone.now()
                inv.save(update_fields=["sent_at"])
                text = f"دعوتنامه: {_name(allowed[uid])} عزیز، شما به «{m.title}» دعوت شده‌اید. زمان: {when}. — {m.school.name}"
                sms += enqueue_user(allowed[uid], m.school, "meeting_invite", text, f"meet:{m.pk}:{uid}")
        audit(request, "meeting.invite", f"meeting:{m.pk} n={len(ids)}")
        return Response({**_meeting_dict(m), "sms_queued": sms})


class InvitationPrintView(APIView):
    """GET /api/meetings/<id>/invitation/<user>/ — the data of the invitation letter (دعوتنامه) for printing / PDF."""

    def get(self, request, pk, user_id):
        m = get_object_or_404(Meeting, pk=pk)
        inv = get_object_or_404(Invitation, meeting=m, invitee_id=user_id)
        u = request.user
        if not ((u.role in MANAGERS and org_in_scope(u, m.school)) or u.pk == user_id):
            raise PermissionDenied()
        return Response({"school": m.school.name, "title": m.title, "kind": m.get_kind_display(),
                         "starts_at": timezone.localtime(m.starts_at).strftime("%Y/%m/%d  %H:%M"),
                         "ends_at": timezone.localtime(m.ends_at).strftime("%Y/%m/%d  %H:%M"),
                         "location": m.location, "agenda": m.agenda, "invitee": _name(inv.invitee)})


class AttendanceView(APIView):
    def put(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        _manager(request.user, m.school)
        for row in request.data.get("attendance") or []:
            Invitation.objects.filter(meeting=m, invitee_id=row.get("invitee")).update(attended=bool(row.get("attended")))
        return Response(_meeting_dict(m))


class DecisionsView(APIView):
    """POST {text, follow_up, due_date} — a decision (مصوبه) and the person who follows it up."""

    def post(self, request, pk):
        m = get_object_or_404(Meeting, pk=pk)
        _manager(request.user, m.school)
        text = str(request.data.get("text", "")).strip()[:2000]
        if not text:
            raise serializers.ValidationError({"text": "متن مصوبه لازم است."})
        follow = None
        if request.data.get("follow_up"):
            follow = get_object_or_404(User, pk=request.data["follow_up"])
            staff_ids = {u.pk for u in _candidates(m.school, "teachers_council")}
            if follow.pk not in staff_ids:
                raise serializers.ValidationError({"follow_up": "فرد پیگیری باید از کادر همین مدرسه باشد."})
        due = None
        if request.data.get("due_date"):
            try:
                due = datetime.fromisoformat(str(request.data["due_date"])[:10]).date()
            except ValueError:
                raise serializers.ValidationError({"due_date": "تاریخ نامعتبر است."})
        d = Decision.objects.create(meeting=m, text=text, follow_up=follow, due_date=due)
        audit(request, "meeting.decision", f"decision:{d.pk}")
        return Response(_meeting_dict(m), status=201)


class DecisionDetailView(APIView):
    def patch(self, request, pk):
        d = get_object_or_404(Decision, pk=pk)
        _manager(request.user, d.meeting.school)
        if "done" in request.data:
            d.done = bool(request.data["done"])
        if "text" in request.data:
            d.text = str(request.data["text"])[:2000]
        d.save()
        return Response(_meeting_dict(d.meeting))

    def delete(self, request, pk):
        d = get_object_or_404(Decision, pk=pk)
        _manager(request.user, d.meeting.school)
        m = d.meeting
        d.delete()
        return Response(_meeting_dict(m))


class MyMeetingsView(APIView):
    """The meetings a person is invited to, and the decisions he follows up."""

    def get(self, request):
        u = request.user
        inv = Invitation.objects.filter(invitee=u).select_related("meeting__school").order_by("-meeting__starts_at")[:200]
        decs = Decision.objects.filter(follow_up=u).select_related("meeting").order_by("done", "due_date")[:200]
        return Response({"invitations": [{"meeting": _meeting_dict(i.meeting, full=False), "attended": i.attended} for i in inv],
                         "decisions": [{"id": d.pk, "text": d.text, "meeting": d.meeting.title, "due_date": d.due_date, "done": d.done} for d in decs]})
