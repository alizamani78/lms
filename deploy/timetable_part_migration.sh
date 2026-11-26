#!/usr/bin/env bash
# اجرای امن مهاجرت نیمه‌ی زنگ: پشتیبان کامل، سپس اعمال تغییر در یک تراکنش.
# استفاده (با root):  bash deploy/timetable_part_migration.sh
set -euo pipefail
DB_NAME="${DB_NAME:-lms}"
BACKUP_DIR="/var/backups/razavi-lms"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "۱) پشتیبان کامل دیتابیس..."
sudo -u postgres pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/before-timetable-part-$STAMP.dump"
echo "   ذخیره شد: $BACKUP_DIR/before-timetable-part-$STAMP.dump"

echo "۲) شمارش خانه‌های برنامه پیش از تغییر..."
sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM timetable_timetableentry;"

echo "۳) اعمال مهاجرت..."
sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$(dirname "$0")/timetable_part_migration.sql"

echo "۴) شمارش پس از تغییر (باید برابر باشد)..."
sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM timetable_timetableentry;"
echo "تمام شد. اگر شمارش‌ها برابر نیست، با این پشتیبان بازگردانی کنید:"
echo "  sudo -u postgres pg_restore -d $DB_NAME --clean $BACKUP_DIR/before-timetable-part-$STAMP.dump"
