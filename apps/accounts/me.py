"""Profile, password change and the list of schools a user may access."""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.models import audit
from apps.core.scope import scope_q
from apps.courses.models import Course, Enrollment

from .mfa import get_confirmed_device, mfa_required_for
from .models import OrgUnit, ParentLink, User


class MeSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    org_chain = serializers.SerializerMethodField()
    mfa_enabled = serializers.SerializerMethodField()
    mfa_required = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "role", "role_label", "org_chain",
                  "mfa_enabled", "mfa_required")
        read_only_fields = ("id", "username", "role")     # a user can never change role or org

    def get_mfa_enabled(self, user):
        return get_confirmed_device(user) is not None

    def get_mfa_required(self, user):
        return mfa_required_for(user)

    def get_org_chain(self, user):
        if not user.org_id:
            return []
        ids = [int(x) for x in user.org.path.strip("/").split("/") if x]
        by_id = {o.pk: o for o in OrgUnit.objects.filter(pk__in=ids)}
        return [{"id": i, "name": by_id[i].name, "kind": by_id[i].kind} for i in ids if i in by_id]


class MeView(APIView):
    def get(self, request):
        data = dict(MeSerializer(request.user).data)
        data["mfa_verified"] = bool(request.auth is not None and request.auth.get("mfa"))
        data["extra_roles"] = [{"role": x.role, "role_label": x.get_role_display(), "school": x.school_id,
                                "school_name": x.school.name if x.school_id else ""} for x in request.user.extra_roles.select_related("school")]
        return Response(data)

    def patch(self, request):
        s = MeSerializer(request.user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        audit(request, "profile.update")
        return Response(s.data)


class PasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, max_length=128)
    new_password = serializers.CharField(write_only=True, max_length=128)


class PasswordView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        s = PasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(s.validated_data["old_password"]):
            raise serializers.ValidationError({"old_password": "رمز فعلی درست نیست."})
        try:
            validate_password(s.validated_data["new_password"], user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})
        user.set_password(s.validated_data["new_password"])
        user.save(update_fields=["password"])
        audit(request, "password.change")
        return Response(status=204)


class SchoolSerializer(serializers.ModelSerializer):
    region = serializers.SerializerMethodField()

    class Meta:
        model = OrgUnit
        fields = ("id", "name", "region")

    def get_region(self, obj):
        return obj.parent.name if obj.parent_id else ""


class SchoolListView(ListAPIView):
    """Students/teachers: their own school plus schools of their courses. Admins: schools in their subtree."""
    serializer_class = SchoolSerializer

    def get_queryset(self):
        u = self.request.user
        qs = OrgUnit.objects.filter(kind=OrgUnit.Kind.SCHOOL).select_related("parent").order_by("name", "id")
        if u.role == User.Role.STUDENT:
            ids = set(Enrollment.objects.filter(student=u).values_list("course__org_id", flat=True))
        elif u.role == User.Role.TEACHER:
            ids = set(Course.objects.filter(teacher=u).values_list("org_id", flat=True))
        elif u.role == User.Role.PARENT:
            ids = set(ParentLink.objects.filter(parent=u, is_verified=True).values_list("student__org_id", flat=True))
            return qs.filter(pk__in=[i for i in ids if i])
        elif u.role in User.ADMIN_ROLES or u.role == User.Role.EXPERT:
            if u.role == User.Role.SUPER_ADMIN:
                return qs
            return qs.filter(scope_q(u, "path"))
        else:
            return qs.none()
        if u.org_id:
            ids.add(u.org_id)
        return qs.filter(pk__in=ids)
