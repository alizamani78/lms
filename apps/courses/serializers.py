from rest_framework import serializers

from apps.accounts.models import OrgUnit, User
from apps.core.scope import org_in_scope

from .models import ContentItem, Course


class CourseSerializer(serializers.ModelSerializer):
    org_name = serializers.CharField(source="org.name", read_only=True)
    teacher_name = serializers.SerializerMethodField()
    classroom_name = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = ("id", "title", "org", "org_name", "teacher", "teacher_name", "classroom", "classroom_name")

    def get_classroom_name(self, obj):
        """The class with its grade, e.g. «دوازدهم — ۳۷۲»; empty when the lesson has no class yet."""
        c = obj.classroom
        return f"{c.grade.name} — {c.name}" if c is not None and c.grade_id else (c.name if c is not None else "")

    def get_teacher_name(self, obj):
        # a lesson arranged automatically has no teacher yet
        if obj.teacher is None:
            return ""
        return obj.teacher.get_full_name() or obj.teacher.username

    def validate_org(self, org):
        user = self.context["request"].user
        if org.kind != OrgUnit.Kind.SCHOOL or not org_in_scope(user, org):
            raise serializers.ValidationError("مدرسه نامعتبر است.")
        return org

    def validate_teacher(self, teacher):
        user = self.context["request"].user
        if teacher.role != User.Role.TEACHER or not teacher.org_id or not org_in_scope(user, teacher.org):
            raise serializers.ValidationError("معلم نامعتبر است.")
        return teacher


class EnrollInput(serializers.Serializer):
    students = serializers.ListField(child=serializers.IntegerField(), max_length=500, allow_empty=False)


class ContentItemSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()

    class Meta:
        model = ContentItem
        fields = ("id", "title", "kind", "body", "order", "is_published", "file")

    def get_file(self, obj):
        return obj.file.as_dict() if obj.file_id else None


class ContentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    kind = serializers.ChoiceField(choices=[c[0] for c in ContentItem.Kind.choices])
    body = serializers.CharField(required=False, allow_blank=True, max_length=200000)
    order = serializers.IntegerField(required=False, min_value=0, max_value=60000, default=0)
    is_published = serializers.BooleanField(required=False, default=False)
    file = serializers.IntegerField(required=False, allow_null=True)


class ContentPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    body = serializers.CharField(required=False, allow_blank=True, max_length=200000)
    order = serializers.IntegerField(required=False, min_value=0, max_value=60000)
    is_published = serializers.BooleanField(required=False)
