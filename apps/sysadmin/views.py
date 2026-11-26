from django.core.exceptions import ValidationError as DjangoVE
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import Subject
from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.permissions import role_required

from .resources import OPTION_MODELS, RESOURCES

SuperOnly = role_required(User.Role.SUPER_ADMIN)


def _writable(r):
    if r.get("readonly"):
        raise PermissionDenied("این بخش فقط‌خواندنی است.")


def _res(name):
    r = RESOURCES.get(name)
    if r is None:
        from django.http import Http404
        raise Http404()
    return r


def _base_qs(r):
    qs = r["qs"]() if "qs" in r else r["model"].objects.all()
    if r.get("kind"):
        qs = qs.filter(kind=r["kind"])
    return qs


def _label(obj):
    """Readable labels: a school is shown with its name, region and province (never a path or a bare province name)."""
    if isinstance(obj, OrgUnit):
        chain = []
        node = obj.parent if obj.kind == "school" else obj
        if obj.kind == "school":
            node = obj.parent
            base = obj.name
            while node is not None:
                if node.kind in ("region", "county", "province"):
                    chain.append(node.name)
                node = node.parent
            return base + (" — " + " — ".join(chain[:2]) if chain else "")
        return obj.name
    return str(obj)


def _user_with_region(user, school):
    """«نام و نام خانوادگی — منطقه» (نزدیک‌ترین منطقه‌ی مدرسه؛ اگر نبود، شهرستان)."""
    name = (user.get_full_name() or user.username or "").strip()
    region = ""
    node = getattr(school, "parent", None) if school is not None else None
    county = ""
    while node is not None:
        if node.kind == "region":
            region = node.name
            break
        if node.kind == "county" and not county:
            county = node.name
        node = node.parent
    place = region or county
    return f"{name} — {place}" if place else name


def _display(obj, field, model_field):
    v = getattr(obj, field)
    if model_field.many_to_many:
        return [_label(x) for x in v.all()]
    if model_field.is_relation:
        # a staff member in a school: full name, and the region (منطقه) of that school
        if field == "user" and v is not None and hasattr(obj, "school"):
            return _user_with_region(v, getattr(obj, "school", None))
        # a level shown with its branch: «متوسطه دوم — کاردانش» (grades, fields, school profiles)
        if field == "level" and v is not None and hasattr(obj, "branch"):
            br = getattr(obj, "branch", None)
            bname = getattr(br, "name", br) or ""
            return f"{v.name} — {bname}" if bname else v.name
        return _label(v) if v is not None else ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    # a choice (role, status, kind …) is shown with its Persian label, not its code
    choices = getattr(model_field, "choices", None)
    if choices and v is not None and not isinstance(v, bool):
        return str(dict(choices).get(v, v))
    return v


def schema_for(keys, resources=None):
    """Describe resources for the browser form builder (labels, types, choices, FK targets)."""
    resources = resources or RESOURCES
    out = []
    for key in keys:
        r = resources[key]
        model = r["model"]
        fields = []
        for name in r["fields"]:
            f = model._meta.get_field(name)
            meta = {"name": name, "label": str(f.verbose_name), "required": not (f.blank or f.has_default()) and not f.null,
                    "readonly": name in r.get("readonly_edit", [])}
            if f.many_to_many:
                meta.update(type="m2m", target=f"{f.related_model._meta.app_label}.{f.related_model.__name__}")
            elif f.is_relation:
                meta.update(type="fk", target=f"{f.related_model._meta.app_label}.{f.related_model.__name__}")
            elif getattr(f, "choices", None):
                choices = [{"value": v, "label": str(l)} for v, l in f.choices]
                if name in r.get("choice_filter", {}):
                    choices = [c for c in choices if c["value"] in r["choice_filter"][name]]
                meta.update(type="choice", choices=choices)
            elif f.get_internal_type() == "BooleanField":
                meta["type"] = "bool"
            elif f.get_internal_type() in ("IntegerField", "PositiveSmallIntegerField", "PositiveIntegerField", "BigIntegerField"):
                meta["type"] = "int"
            elif f.get_internal_type() == "DateField":
                meta["type"] = "date"
            elif f.get_internal_type() == "DateTimeField":
                meta["type"] = "datetime"
            elif f.get_internal_type() == "TimeField":
                meta["type"] = "time"
            else:
                meta["type"] = "text"
            if name in r.get("pick", {}):
                meta["pick"] = r["pick"][name]
            if name in r.get("optional", []):
                meta["required"] = False
            fields.append(meta)
        out.append({"key": key, "rules": r.get("rules"), "help": r.get("help", ""), "label": r["label"], "section": r["section"], "fields": fields, "list": r["list"],
                    "virtual_fields": [{"name": n, "label": lab} for n, (_fn, lab) in (r.get("virtual") or {}).items()],
                    "search": bool(r.get("search")), "readonly": bool(r.get("readonly")), "unit": bool(r.get("unit_lookup")),
                    "filters": list((r.get("filters") or {}).keys()),
                    "deletable": r.get("deletable", True)})
    return out


class SchemaView(APIView):
    permission_classes = [SuperOnly]

    def get(self, request):
        return Response(schema_for(list(RESOURCES.keys())))


class OrgCodeView(APIView):
    """GET /api/system/org-code/?id=<org id> -> {code, name}: the code entered for that school or unit."""
    permission_classes = [SuperOnly]

    def get(self, request):
        o = OrgUnit.objects.filter(pk=request.query_params.get("id") or 0).first()
        if o is None:
            return Response({"code": "", "name": ""})
        return Response({"code": o.code or "", "name": o.name})


class OptionsView(APIView):
    permission_classes = [SuperOnly]

    def get(self, request):
        from .options import build_options
        return Response(build_options(request, scoped=False))


class ScopedMixin:
    """Overridden by the role-scoped management API (apps/sysadmin/scoped.py)."""

    def resource(self, res):
        return _res(res)

    def queryset(self, r):
        return _base_qs(r)

    def deletable(self, r):
        return True


class ResourceListView(ScopedMixin, APIView):
    permission_classes = [SuperOnly]

    def get(self, request, res):
        r = self.resource(res)
        qs = self.queryset(r)
        pr = request.query_params
        if pr.get("unit", "").isdigit() and r.get("unit_lookup"):
            from apps.accounts.models import OrgUnit
            unit = OrgUnit.objects.filter(pk=int(pr["unit"])).first()
            qs = qs.filter(**{r["unit_lookup"] + "__startswith": unit.path}) if unit else qs.none()
        for param, field in (r.get("filters") or {}).items():
            val = pr.get(param, "")
            if val != "":
                qs = qs.filter(**{field: (val == "true") if val in ("true", "false") else val})
        q = request.query_params.get("q", "").strip()[:80]
        if q and r.get("search"):
            # every typed word must match one of the search fields, so «علی احمدی» finds the person by first and last name
            for word in q.split():
                cond = Q()
                for f in r["search"]:
                    cond |= Q(**{f"{f}__icontains": word})
                qs = qs.filter(cond)
        # without a search: the first 500 rows; with a search: every match up to 5000 (so one person can be found)
        cap = 5000 if q else 500
        rows = list(qs.order_by("pk")[:cap + 1])
        truncated = len(rows) > cap
        rows = rows[:cap]
        model = r["model"]
        return Response({"truncated": truncated, "columns": r["list"], "rows": [
            {"id": o.pk, **{f: (r["virtual"][f][0](o) if f in (r.get("virtual") or {}) else _display(o, f, model._meta.get_field(f))) for f in r["list"]}} for o in rows]})


class ResourceFormMixin:
    def _payload(self, r, data, instance=None):
        model = r["model"]
        values, m2m = {}, {}
        for name in r["fields"]:
            if instance is not None and name in r.get("readonly_edit", []):
                continue
            if name not in data:
                continue
            f = model._meta.get_field(name)
            if name == "level" and isinstance(data.get(name), str) and ":" in data.get(name):
                lid, _, br = data[name].partition(":")
                if not lid.isdigit():
                    raise serializers.ValidationError({"level": "مقطع را انتخاب کنید."})
                values["level_id"] = int(lid)
                bf = next((x for x in model._meta.fields if x.name == "branch"), None)
                if bf is not None and bf.is_relation:
                    from apps.academics.models import Branch
                    values["branch_id"] = Branch.objects.get_or_create(level_id=int(lid), name=br)[0].pk if br else None
                else:
                    values["branch"] = br[:40]
                continue
            if f.many_to_many:
                ids = data.get(name) or []
                if not isinstance(ids, list) or len(ids) > 200:
                    raise serializers.ValidationError({name: "فهرست نامعتبر است."})
                m2m[name] = ids
            elif f.is_relation:
                values[f.attname] = data.get(name) or None
            else:
                v = data.get(name)
                if v is None or v == "":
                    # NOT NULL columns take "" (blank); only nullable columns take None
                    values[name] = None if getattr(f, "null", False) else ""
                else:
                    values[name] = v
        if instance is None and r.get("kind"):
            if r.get("kind") == "school":
                # a school is created only through the school form (with its profile and code), never from a generic list
                raise serializers.ValidationError({"name": "ایجاد مدرسه فقط از «مشخصات مدارس» و با کد مدرسه انجام می‌شود."})
            values["kind"] = r["kind"]
        return values, m2m

    def _save(self, request, r, instance, data, is_new):
        _writable(r)
        model = r["model"]
        if model.__name__ == "Subject":
            # a lesson can not be entered twice: the same name in the same grade is refused, and so is a repeated code
            from apps.academics.subject_dedupe import find_twin
            from apps.academics.models import Subject as _Sub
            code = str(data.get("code") or "").strip()
            if code and _Sub.objects.filter(code=code).exclude(pk=getattr(instance, "pk", None)).exists():
                raise serializers.ValidationError({"code": "این کد درس قبلاً ثبت شده است."})
            grade_ids = [int(x) for x in (data.get("grades") or []) if str(x).isdigit()]
            twin = find_twin(str(data.get("name") or ""), grade_ids, exclude_pk=getattr(instance, "pk", None)) if grade_ids else None
            if twin is not None:
                raise serializers.ValidationError({"name": f"درسی با همین نام در این پایه قبلاً ثبت شده است (کد {twin.code})."})
        values, m2m = self._payload(r, data, None if is_new else instance)
        try:
            with transaction.atomic():
                if is_new:
                    obj = model(**values)
                else:
                    obj = instance
                    for k, v in values.items():
                        setattr(obj, k, v)
                hook = getattr(self, "before_save", None)
                if hook:
                    hook(r, obj, is_new)
                # password is never set through this API (use the reset endpoint); it is excluded from validation
                if r.get("before_validate"):
                    r["before_validate"](obj)
                obj.full_clean(exclude=["password", "last_login"])
                if r.get("prepare"):
                    r["prepare"](obj)
                obj.save()
                for name, ids in m2m.items():
                    getattr(obj, name).set(ids)
                if isinstance(obj, Subject):
                    problems = obj.link_problems(list(obj.grades.all()), list(obj.study_fields.all()))
                    if problems:
                        raise DjangoVE(problems)
        except DjangoVE as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else {"detail": e.messages})
        except ValueError as e:
            raise serializers.ValidationError({"detail": str(e)[:200]})
        audit(self.request, ("sys.create." if is_new else "sys.update.") + res_key(r), f"{model.__name__}:{obj.pk}")
        return obj


def res_key(r):
    return r["model"].__name__.lower()


class ResourceCreateView(ScopedMixin, ResourceFormMixin, APIView):
    permission_classes = [SuperOnly]

    def post(self, request, res):
        r = self.resource(res)
        obj = self._save(request, r, None, request.data or {}, is_new=True)
        return Response({"id": obj.pk}, status=201)


def _dependency_message(obj, protected):
    """Why an item cannot be deleted: which records depend on it, how many, and an example of each kind."""
    from collections import OrderedDict
    groups = OrderedDict()
    for dep in list(protected)[:500]:
        name = str(dep)[:60]
        key = dep._meta.verbose_name_plural
        groups.setdefault(key, {"count": 0, "example": name})
        groups[key]["count"] += 1
    parts = [f"«{k}» ({v['count']} مورد، مثل «{v['example']}»)" for k, v in groups.items()]
    head = f"«{obj}» حذف نشد، چون به این بخش‌ها وصل است: " + "، ".join(parts) if parts else f"«{obj}» حذف نشد، چون اطلاعات وابسته دارد."
    return head + ". ابتدا این موارد را جابه‌جا یا حذف کنید؛ یا به‌جای حذف، آن را غیرفعال کنید."


class ResourceDetailView(ScopedMixin, ResourceFormMixin, APIView):
    permission_classes = [SuperOnly]

    def get(self, request, res, pk):
        r = self.resource(res)
        o = get_object_or_404(self.queryset(r), pk=pk)
        model = r["model"]
        labels = {}
        for f in r["fields"]:
            mf = model._meta.get_field(f)
            if mf.is_relation and not mf.many_to_many and getattr(o, mf.attname):
                labels[f] = _label(getattr(o, f))
            elif mf.many_to_many:
                labels[f] = {str(x.pk): _label(x) for x in getattr(o, f).all()}
        kinds = {}
        for f in r["fields"]:
            mf = model._meta.get_field(f)
            if mf.is_relation and not mf.many_to_many and mf.related_model.__name__ == "OrgUnit" and getattr(o, mf.attname):
                kinds[f] = getattr(o, f).kind
        out = {"id": o.pk, "labels": labels, "kinds": kinds, "values": {
            f: (list(getattr(o, f).values_list("pk", flat=True)) if model._meta.get_field(f).many_to_many
                else (getattr(o, model._meta.get_field(f).attname) if model._meta.get_field(f).is_relation
                      else (lambda v: v.isoformat() if hasattr(v, "isoformat") else v)(getattr(o, f))))
            for f in r["fields"]}}
        if "level" in r["fields"] and hasattr(model, "branch") and getattr(o, "level_id", None):
            br = getattr(o, "branch", "")
            bname = getattr(br, "name", br) or ""
            out["values"]["level"] = f"{o.level_id}:{bname}"
            lvl = getattr(o, "level", None)
            if lvl is not None:
                out["labels"]["level"] = f"{lvl.name}" + (f" — {bname}" if bname else "")
        return Response(out)

    def patch(self, request, res, pk):
        r = self.resource(res)
        o = get_object_or_404(self.queryset(r), pk=pk)
        self._save(request, r, o, request.data or {}, is_new=False)
        return Response({"id": o.pk})

    def delete(self, request, res, pk):
        r = self.resource(res)
        _writable(r)
        if not self.deletable(r):
            raise PermissionDenied("حذف این مورد مجاز نیست؛ به‌جای آن غیرفعالش کنید.")
        o = get_object_or_404(self.queryset(r), pk=pk)
        if o.pk == request.user.pk:
            raise serializers.ValidationError({"detail": "نمی‌توانید حساب خودتان را حذف کنید."})
        try:
            with transaction.atomic():
                o.delete()
        except ProtectedError as e:
            return Response({"detail": _dependency_message(o, e.protected_objects)}, status=409)
        audit(request, "sys.delete." + res_key(r), f"pk:{pk}")
        return Response(status=204)


class StatsView(APIView):
    """Dashboard numbers for the system panel (counts only)."""
    permission_classes = [SuperOnly]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone
        from apps.accounts.models import OrgUnit
        from apps.academics.models import ClassRoom
        from apps.appeals.models import GradeAppeal
        from apps.courses.models import Course
        from apps.exams.models import Attempt
        from apps.transfers.models import TransferRequest
        today = timezone.now() - timedelta(days=1)
        roles = {r: User.objects.filter(role=r, is_active=True).count() for r, _ in User.Role.choices}
        return Response({
            "users": roles,
            "schools": OrgUnit.objects.filter(kind="school").count(),
            "classes": ClassRoom.objects.count(),
            "courses": Course.objects.count(),
            "exams_submitted_24h": Attempt.objects.filter(submitted_at__gte=today).count(),
            "open_appeals": GradeAppeal.objects.filter(status__in=["open", "escalated"]).count(),
            "pending_transfers": TransferRequest.objects.filter(status__in=["pending_source", "pending_dest", "ready"]).count(),
        })
