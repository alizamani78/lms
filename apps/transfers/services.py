from django.db import transaction
from django.db.models import Q

from apps.academics.models import ClassRoom
from apps.accounts.models import ParentLink, User
from apps.core.scope import org_in_scope
from apps.courses.models import Course, Enrollment

from .models import TransferRequest


def in_scope(user, school):
    """Admin roles act only on schools inside their own subtree (super admins: all)."""
    if user.role not in User.ADMIN_ROLES:
        return False
    return org_in_scope(user, school)


def execute(transfer, student, to_classroom):
    """Apply a READY transfer. Caller holds locks. Past grades, attempts, attendance and submissions stay untouched;
    only the CURRENT school-year membership moves."""
    same_school = transfer.from_school_id == transfer.to_school_id
    old_classes = list(ClassRoom.objects.filter(school=transfer.from_school, students=student,
                                                academic_year__is_current=True))
    current_year = Q(academic_year__is_current=True) | Q(academic_year__isnull=True)
    stale = Enrollment.objects.filter(current_year, student=student, course__org=transfer.from_school)
    if same_school:      # class change: keep school-wide courses, leave only the old class's courses
        stale = stale.filter(course__classroom__in=old_classes)
    removed_courses = list(stale.values_list("course_id", flat=True))
    stale.delete()
    for c in old_classes:
        c.students.remove(student)
    if not same_school:
        student.org = transfer.to_school
        student.save(update_fields=["org"])
        # the new school must re-verify the guardian link
        ParentLink.objects.filter(student=student).update(is_verified=False)
    added_courses = []
    if to_classroom is not None:
        to_classroom.students.add(student)
        courses = list(Course.objects.filter(classroom=to_classroom))
        Enrollment.objects.bulk_create(
            [Enrollment(course=c, student=student, academic_year_id=c.academic_year_id) for c in courses],
            ignore_conflicts=True)
        added_courses = [c.pk for c in courses]
    transfer.summary = {"removed_classrooms": [c.pk for c in old_classes], "removed_course_enrollments": removed_courses,
                        "added_course_enrollments": added_courses}
    transfer.status = TransferRequest.Status.COMPLETED
