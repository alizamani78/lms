"""یک‌بار برای همیشه: هر جدول و ستونی را که در مدل‌ها هست اما در دیتابیس نیست، پیدا و اضافه می‌کند.

  - فقط می‌سازد؛ هیچ ستونی را حذف یا تغییر نمی‌دهد و هیچ داده‌ای پاک نمی‌شود.
  - حالت پیش‌فرض فقط SQL را نشان می‌دهد؛ با --apply اجرا می‌شود.
  - اتصال با همان کاربر برنامه (DB_USER) انجام می‌شود تا جدول‌های جدید مالک درست داشته باشند.

اجرا (از ریشه‌ی پروژه، با دسترسی root):
  set -a; . /etc/razavi-lms/.env; set +a
  /opt/razavi-lms/venv/bin/python deploy/sync_missing_columns.py           # فقط نمایش
  /opt/razavi-lms/venv/bin/python deploy/sync_missing_columns.py --apply   # اجرا
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.db import connection  # noqa: E402


def plan(editor):
    """ترتیب کار: جدول‌های ناموجود، سپس ستون‌های ناموجود، سپس جدول‌های میانی M2M که ساخته نشده‌اند.
    ستون‌هایی که در مدل اختیاری‌اند ولی در دیتابیس NOT NULL مانده‌اند هم اصلاح می‌شوند (DROP NOT NULL)."""
    existing = set(connection.introspection.table_names())
    added_tables, added_cols = [], []
    nullable_fix = []
    with connection.cursor() as cur:
        for model in apps.get_models(include_auto_created=False):
            if not model._meta.managed or model._meta.proxy:
                continue
            table = model._meta.db_table
            if table not in existing:
                editor.create_model(model)
                existing.add(table)
                added_tables.append(table)
                continue
            info = {c.name: c for c in connection.introspection.get_table_description(cur, table)}
            for field in model._meta.local_fields:
                if field.column and field.column not in info:
                    editor.add_field(model, field)
                    added_cols.append(f"{table}.{field.column}")
                elif field.column and field.null and not info[field.column].null_ok:
                    nullable_fix.append(f'ALTER TABLE "{table}" ALTER COLUMN "{field.column}" DROP NOT NULL;')
            for field in model._meta.local_many_to_many:
                through = field.remote_field.through
                if through._meta.auto_created and through._meta.db_table not in existing:
                    editor.create_model(through)
                    existing.add(through._meta.db_table)
                    added_tables.append(through._meta.db_table)
    return added_tables, added_cols, nullable_fix


def main():
    apply = "--apply" in sys.argv
    # 1) نمایش SQL (بدون تغییر در دیتابیس)
    with connection.schema_editor(collect_sql=True, atomic=False) as editor:
        tables, cols, nulls = plan(editor)
        sql = list(editor.collected_sql) + nulls
    print(f"جدول‌های ناموجود: {len(tables)}  |  ستون‌های ناموجود: {len(cols)}")
    for t in tables:
        print("  + جدول:", t)
    for c in cols:
        print("  + ستون:", c)
    if not sql:
        print("همه‌ی جدول‌ها و ستون‌ها در دیتابیس هستند. کاری لازم نیست.")
        return
    print("\n--- SQL ---")
    for stmt in sql:
        print(stmt.rstrip(";") + ";")
    if not apply:
        print("\nهنوز چیزی اجرا نشد. برای اجرا همین فرمان را با --apply بزنید.")
        return
    # the connections' name used to be unique over the whole system; it is now unique per school
    sql.append(
        "DO $$ DECLARE r record; BEGIN FOR r IN SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE t.relname = 'integrations_externalsystem' AND c.contype = 'u' AND c.conkey = ARRAY["
        "(SELECT attnum FROM pg_attribute WHERE attrelid = t.oid AND attname = 'name')]::smallint[] "
        "LOOP EXECUTE 'ALTER TABLE integrations_externalsystem DROP CONSTRAINT ' || quote_ident(r.conname); END LOOP; END $$;"
    ) if connection.vendor == "postgresql" else None
    sql = [x for x in sql if x]
    # 2) اجرا (هر ستون به‌تنهایی؛ اگر یکی خطا داد، بقیه ادامه می‌یابند و خطاها گزارش می‌شوند)
    done, failed = 0, []
    for stmt in sql:
        try:
            with connection.cursor() as cur:
                cur.execute(stmt)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((stmt[:120], str(exc)[:200]))
    print(f"\nاجرا شد: {done} دستور  |  ناموفق: {len(failed)}")
    for s, e in failed:
        print("  ✗", s, "→", e)


if __name__ == "__main__":
    main()
