from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import kartabl_filter, org_in_scope, org_scope_q

from .models import ContentItem, Course, Enrollment
from .serializers import (ContentCreateSerializer, ContentItemSerializer, ContentPatchSerializer, CourseSerializer,
                          EnrollInput)

STAFF = role_required(*User.STAFF_ROLES)


class CourseViewSet(viewsets.ModelViewSet):
    serializer_class = CourseSerializer
    http_method_names = ["get", "post", "put", "patch", "head", "options"]   # no delete over the API

    def get_permissions(self):
        if self.action in ("list", "retrieve", "content"):
            return super().get_permissions()
        return [STAFF()]

    def get_queryset(self):
        u = self.request.user
        qs = Course.objects.select_related("org", "teacher").filter(**kartabl_filter(self.request, "org_id"))
        g, f = self.request.query_params.get("grade", ""), self.request.query_params.get("field", "")
        if g.isdigit():
            qs = qs.filter(Q(classroom__grade_id=int(g)) | Q(subject__grades__id=int(g))).distinct()
        if f.isdigit():
            qs = qs.filter(Q(classroom__study_field_id=int(f)) | Q(subject__study_fields__id=int(f))).distinct()
        org = self.request.query_params.get("org", "")
        if org.isdigit():
            qs = qs.filter(org_id=int(org))
        if u.role == User.Role.STUDENT:
            qs = qs.filter(enrollments__student=u)
        elif u.role == User.Role.TEACHER:
            qs = qs.filter(teacher=u)
        elif u.role in User.ADMIN_ROLES or u.role == User.Role.EXPERT:
            qs = qs.filter(org_scope_q(u, "org"))
        else:
            qs = qs.none()
        return qs.order_by("id")

    def perform_create(self, serializer):
        u = self.request.user
        if u.role == User.Role.TEACHER:
            if not u.org_id:
                raise PermissionDenied()
            serializer.save(teacher=u, org=u.org)
        else:
            serializer.save()

    @action(detail=True, methods=["get", "post"])
    def content(self, request, pk=None):
        course = self.get_object()          # scope-filtered: not enrolled / not yours => 404
        if request.method == "POST":
            if request.user.role not in User.STAFF_ROLES:
                raise PermissionDenied()
            s = ContentCreateSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            data = dict(s.validated_data)
            file_id = data.pop("file", None)
            asset = None
            if file_id:
                from apps.files.models import FileAsset
                asset = FileAsset.objects.filter(pk=file_id, owner=request.user).first()
                if asset is None:
                    raise serializers.ValidationError({"file": "فایل نامعتبر است."})
            item = ContentItem(course=course, created_by=request.user, file=asset, **data)
            try:
                item.full_clean()           # https-only URLs, text required for text items, ...
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.message_dict)
            item.save()
            audit(request, "content.create", f"content:{item.pk}")
            return Response({"id": item.pk}, status=201)
        items = course.content_items.select_related("file")
        if request.user.role == User.Role.STUDENT:
            items = items.filter(is_published=True)
        return Response(ContentItemSerializer(items.order_by("order", "id")[:500], many=True).data)

    @action(detail=True, methods=["get"])
    def students(self, request, pk=None):
        """Students enrolled in the course (for choosing assignment recipients)."""
        course = self.get_object()
        if request.user.role not in User.STAFF_ROLES:
            raise PermissionDenied()
        from .roster import roster_ids
        rows = User.objects.filter(pk__in=roster_ids(course)).order_by("last_name", "username")[:500]
        return Response([{"id": u.pk, "username": u.username, "name": u.get_full_name()} for u in rows])

    @action(detail=True, methods=["post"])
    def enroll(self, request, pk=None):
        course = self.get_object()          # scope-filtered => 404 when out of scope
        s = EnrollInput(data=request.data)
        s.is_valid(raise_exception=True)
        ids = set(s.validated_data["students"])
        students = User.objects.filter(pk__in=ids, role=User.Role.STUDENT).select_related("org")
        valid = [st.pk for st in students if st.org_id and org_in_scope(request.user, st.org)
                 and st.org.path.startswith(course.org.path)]
        with transaction.atomic():
            Enrollment.objects.bulk_create(
                [Enrollment(course=course, student_id=i, academic_year_id=course.academic_year_id) for i in valid],
                ignore_conflicts=True)
        audit(request, "course.enroll", f"course:{course.pk} n={len(valid)}")
        return Response({"enrolled": len(valid), "rejected": len(ids) - len(valid)})


class ContentItemView(APIView):
    """PATCH (publish/unpublish, rename, reorder) and DELETE of one content item, inside the editor's scope."""

    def _item(self, request, pk):
        if request.user.role not in User.STAFF_ROLES:
            raise PermissionDenied()
        qs = ContentItem.objects.select_related("course")
        u = request.user
        qs = qs.filter(course__teacher=u) if u.role == User.Role.TEACHER else qs.filter(org_scope_q(u, "course__org"))
        return get_object_or_404(qs, pk=pk)

    def patch(self, request, pk):
        item = self._item(request, pk)
        s = ContentPatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        for k, v in s.validated_data.items():
            setattr(item, k, v)
        try:
            item.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else {"detail": e.messages})
        item.save()
        audit(request, "content.update", f"content:{item.pk}")
        return Response(ContentItemSerializer(item).data)

    def delete(self, request, pk):
        item = self._item(request, pk)
        audit(request, "content.delete", f"content:{item.pk}")
        item.delete()
        return Response(status=204)
