from django.db.models import Q

from apps.accounts.models import User
from apps.core.scope import org_scope_q


def can_download(user, asset):
    """Who may read a file: its owner, and people who legitimately see the thing it is attached to.
    Student submissions are visible ONLY to the student and the course teacher (never to school/region admins)."""
    from apps.courses.models import ContentItem
    from apps.coursework.models import Assignment, Submission
    if asset.owner_id == user.pk or user.role == User.Role.SUPER_ADMIN:
        return True
    if user.role == User.Role.STUDENT:
        if ContentItem.objects.filter(is_published=True, course__enrollments__student=user).filter(
                Q(file=asset) | Q(embedded_files=asset)).exists():
            return True
        return Assignment.objects.filter(attachments=asset, is_published=True, course__enrollments__student=user) \
            .filter(Q(audience="all") | Q(targets=user)).exists()
    if user.role == User.Role.TEACHER:
        return (ContentItem.objects.filter(course__teacher=user).filter(Q(file=asset) | Q(embedded_files=asset)).exists()
                or Assignment.objects.filter(attachments=asset, course__teacher=user).exists()
                or Submission.objects.filter(files=asset, assignment__course__teacher=user).exists())
    if user.role in User.ADMIN_ROLES:
        return (ContentItem.objects.filter(Q(file=asset) | Q(embedded_files=asset)).filter(org_scope_q(user, "course__org")).exists()
                or Assignment.objects.filter(attachments=asset).filter(org_scope_q(user, "course__org")).exists())
    return False
