"""یکتایی درس‌ها: یک درس با یک نام (بعد از یکسان‌سازی حروف) در یک پایه فقط یک بار وجود دارد.
- find_duplicates(): گروه‌های تکراری را پیدا می‌کند.
- merge(keep, drop): درس تکراری را در درس اصلی ادغام می‌کند (همه‌ی ارجاع‌ها منتقل می‌شوند).
- find_twin(name, grade_ids, exclude_pk): برای جلوگیری از ثبت درس تکراری هنگام ایجاد."""
import re
from collections import defaultdict

from django.db import transaction

from .models import Subject

_ZWNJ = "\u200c"


def normalize(name):
    s = (name or "").replace(_ZWNJ, " ").replace("ي", "ی").replace("ك", "ک")
    s = re.sub(r"[\s\u200c]+", " ", s).strip()
    return s


def find_duplicates():
    groups = defaultdict(list)
    for sub in Subject.objects.prefetch_related("grades").order_by("pk"):
        key_names = normalize(sub.name)
        for g in sub.grades.all():
            groups[(key_names, g.pk)].append(sub)
    out = []
    for (name, gid), subs in groups.items():
        uniq = []
        for s in subs:
            if s not in uniq:
                uniq.append(s)
        if len(uniq) > 1:
            out.append({"name": name, "grade": gid, "subjects": [s.pk for s in uniq]})
    return out


def find_twin(name, grade_ids, exclude_pk=None):
    """درسی با همان نام در یکی از همین پایه‌ها (غیر از خودش)."""
    target = normalize(name)
    qs = Subject.objects.filter(grades__id__in=grade_ids).distinct()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for s in qs:
        if normalize(s.name) == target:
            return s
    return None


@transaction.atomic
def merge(keep, drop):
    """ارجاع‌های درس drop را به keep منتقل می‌کند و drop را حذف می‌کند."""
    for rel in Subject._meta.related_objects:
        field = rel.field
        model = rel.related_model
        if rel.many_to_many:
            continue
        if getattr(field, "related_model", None) is Subject:
            model.objects.filter(**{field.name: drop}).update(**{field.name: keep})
    for m2m in ("grades", "study_fields"):
        for obj in getattr(drop, m2m).all():
            getattr(keep, m2m).add(obj)
    drop.delete()
