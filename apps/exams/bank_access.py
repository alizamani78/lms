"""دسترسی به بانک سؤال: پیش‌فرض فقط خود معلم؛ با انتشار، مدرسه، منطقه، استان یا واحد انتخابی هم می‌بینند.
مدیر مدرسه سؤال‌های همه‌ی معلمان مدرسه‌ی خودش را می‌بیند."""
from django.db.models import Q


def visible_to(user, school=None):
    """فیلتر سؤال‌های بانکی که این کاربر می‌تواند ببیند (و در آزمون خود بیاورد)."""
    if user.role == "super_admin":
        from apps.exams.models import BankQuestion
        return BankQuestion.objects.all()
    from apps.exams.models import BankQuestion
    q = Q(created_by=user)
    org = school or getattr(user, "org", None)
    if org is not None and getattr(org, "path", ""):
        # school-wide questions of the school
        q |= Q(scope="school", org=org)
        # questions shared at a region or province level that contain this school
        from apps.accounts.models import OrgUnit
        ancestors = [a.pk for a in OrgUnit.objects.all() if a.path and org.path.startswith(a.path)]
        q |= Q(scope__in=["region", "province"], share_org_id__in=ancestors)
    if user.role == "school_admin" and org is not None:
        # the principal sees every teacher's questions of his school
        q |= Q(org=org)
    return BankQuestion.objects.filter(q).distinct()
