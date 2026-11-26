#!/usr/bin/env bash
# مدیریت کامل سامانه روی Ubuntu: نصب اولیه، به‌روزرسانی با حفظ PostgreSQL و Redis، بازگشت، و راه‌اندازی خودکار با ریست سرور.
#   sudo ./razavi-lms-manage.sh install  /tmp/razavi_lms.zip     نصب اولیه (یک بار)
#   sudo ./razavi-lms-manage.sh update   /tmp/razavi_lms.zip     به‌روزرسانی (داده‌ها حفظ می‌شوند)
#   sudo ./razavi-lms-manage.sh rollback                          بازگشت به نسخه‌ی قبلی کد
#   sudo ./razavi-lms-manage.sh status                            وضعیت سرویس‌ها
#   sudo ./razavi-lms-manage.sh boot-setup                        فعال‌سازی راه‌اندازی خودکار با ریست سرور
set -Eeuo pipefail

APP_USER="lms"
BASE="/opt/razavi-lms"
APP="$BASE/app"
RELEASES="$BASE/releases"
VENV="$BASE/venv"
ENV_FILE="/etc/razavi-lms/.env"
BACKUP_DIR="/var/backups/razavi-lms"
DB_NAME=""   # نام دیتابیس از فایل تنظیمات خوانده می‌شود (DB_NAME)
SERVICE="razavi-lms.service"
KEEP_RELEASES=5
STAMP="$(date +%Y%m%d-%H%M%S)"

log()  { echo -e "\e[1;36m[$(date +%H:%M:%S)]\e[0m $*" >&2; }   # روی خطا (stderr) تا مسیر نسخه‌ی تازه پاک نشود
die()  { echo -e "\e[1;31mخطا:\e[0m $*" >&2; exit 1; }
need_root() { [[ $EUID -eq 0 ]] || die "این دستور را با sudo اجرا کنید."; }

env_value() {  # خواندن یک مقدار از .env بدون نمایش آن
  grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

has_key() {  # has_key FILE KEY → 0 اگر متغیر در فایل با مقدار غیرخالی باشد
  [[ -f "$1" ]] && tr -d '\r' < "$1" | grep -qE "^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=[[:space:]]*[^[:space:]]"
}

get_key() {  # get_key FILE KEY → مقدار خام (بدون چاپ در لاگ)
  tr -d '\r' < "$1" | grep -E "^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=" | head -1 | sed -E 's/^[^=]*=//'
}

check_env() {
  # اسکریپت خودش تنظیمات گم‌شده را تکمیل می‌کند؛ فقط موارد حساس که هیچ‌جا نیستند باعث توقف می‌شوند
  [[ -f "$ENV_FILE" ]] || { install -d -m 750 /etc/razavi-lms; : > "$ENV_FILE"; chmod 600 "$ENV_FILE"; }
  cp -a "$ENV_FILE" "$ENV_FILE.bak-$STAMP"
  local added=()
  # ۱) کلیدهایی که فقط در نسخه‌ی جاری برنامه (app/.env) هستند، به فایل اصلی منتقل شوند
  for k in DJANGO_SECRET_KEY DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT DB_SSLMODE REDIS_CACHE_URL CELERY_BROKER_URL JWT_SIGNING_KEY PRIVATE_MEDIA_ROOT; do
    if ! has_key "$ENV_FILE" "$k" && has_key "$APP/.env" "$k"; then
      printf '%s=%s\n' "$k" "$(get_key "$APP/.env" "$k")" >> "$ENV_FILE"; added+=("$k")
    fi
  done
  # ۲) مقادیر امن پیش‌فرض برای این سرور
  if ! has_key "$ENV_FILE" DB_HOST; then printf 'DB_HOST=127.0.0.1\n' >> "$ENV_FILE"; added+=(DB_HOST); fi
  if ! has_key "$ENV_FILE" DB_PORT; then printf 'DB_PORT=5432\n' >> "$ENV_FILE"; added+=(DB_PORT); fi
  if ! has_key "$ENV_FILE" PRIVATE_MEDIA_ROOT; then
    mkdir -p "$BASE/private_media"; chown "$APP_USER:$APP_USER" "$BASE/private_media"; chmod 750 "$BASE/private_media"
    printf 'PRIVATE_MEDIA_ROOT=%s\n' "$BASE/private_media" >> "$ENV_FILE"; added+=(PRIVATE_MEDIA_ROOT)
  fi
  if ! has_key "$ENV_FILE" JWT_SIGNING_KEY; then
    printf 'JWT_SIGNING_KEY=%s\n' "$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')" >> "$ENV_FILE"; added+=(JWT_SIGNING_KEY)
    log "کلید امضای توکن ساخته شد؛ کاربران یک‌بار دوباره وارد می‌شوند."
  fi
  chmod 600 "$ENV_FILE"; chown root:root "$ENV_FILE"
  (( ${#added[@]} )) && log "تنظیمات تکمیل شد: ${added[*]} (مقدارها در لاگ نمایش داده نمی‌شوند)"
  # ۳) چیزهایی که نه در فایل اصلی هستند و نه در نسخه‌ی جاری، توقف می‌دهند (نام‌ها، نه مقدارها)
  local missing=()
  for k in DJANGO_SECRET_KEY DB_NAME DB_USER DB_PASSWORD REDIS_CACHE_URL CELERY_BROKER_URL; do
    has_key "$ENV_FILE" "$k" || missing+=("$k")
  done
  if (( ${#missing[@]} )); then
    echo -e "\e[1;31mاین مقادیر در هیچ‌کدام از فایل‌ها پیدا نشد:\e[0m ${missing[*]}" >&2
    echo "یک نسخه‌ی پشتیبان از تنظیمات در $ENV_FILE.bak-$STAMP است. مقدار این موارد را اضافه کنید و دوباره اجرا کنید." >&2
    die "تنظیمات ناقص است."
  fi
  return 0
}

ensure_user_and_dirs() {
  id "$APP_USER" &>/dev/null || useradd --system --home "$BASE" --shell /usr/sbin/nologin "$APP_USER"
  mkdir -p "$BASE" "$RELEASES" "$BACKUP_DIR" /etc/razavi-lms
  chown -R "$APP_USER:$APP_USER" "$BASE"
}

install_packages() {
  log "نصب بسته‌های سیستم"
  apt-get update -y
  apt-get install -y python3 python3-venv python3-dev build-essential libpq-dev \
    postgresql redis-server nginx unzip curl
}

enable_boot_services() {
  # سرویس‌های پایه باید پیش از برنامه و با هر ریست بالا بیایند
  for s in postgresql nginx; do
    systemctl enable "$s" >/dev/null 2>&1 || true
    systemctl start "$s" || true
  done
  # Redis سامانه دو نمونه‌ی جدا دارد (کش و صف)؛ سرویس پیش‌فرض redis-server روی همان پورت ۶۳۷۹ تداخل می‌کند
  systemctl disable --now redis-server >/dev/null 2>&1 || true
  for s in redis-razavi-cache redis-razavi-broker; do
    if systemctl list-unit-files | grep -q "^$s.service"; then
      systemctl enable "$s" >/dev/null 2>&1 || true
      systemctl start "$s" || true
    fi
  done
}

backup_all() {
  log "بکاپ کامل: دیتابیس، Redis، تنظیمات و فایل‌های آپلودی"
  mkdir -p "$BACKUP_DIR"
  DB_NAME="$(get_key "$ENV_FILE" DB_NAME)"
  [[ -n "$DB_NAME" ]] || die "نام دیتابیس (DB_NAME) در تنظیمات نیست؛ بکاپ انجام نشد و کار متوقف شد."
  # بکاپ دیتابیس واقعی الزامی است؛ اگر نبود، به‌روزرسانی متوقف می‌شود (داده‌ی مدرسه نباید بدون بکاپ تغییر کند)
  sudo -u postgres psql -lqt | cut -d'|' -f1 | grep -qw "$DB_NAME" || die "دیتابیس «$DB_NAME» روی سرور پیدا نشد؛ هیچ تغییری انجام نشد."
  sudo -u postgres pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/db-$STAMP.dump"
  pg_restore -l "$BACKUP_DIR/db-$STAMP.dump" >/dev/null || die "بکاپ دیتابیس معتبر نیست."
  log "بکاپ دیتابیس $DB_NAME: db-$STAMP.dump"
  if systemctl is-active --quiet redis-server; then
    redis-cli BGSAVE >/dev/null 2>&1 || true
    sleep 2
    [[ -f /var/lib/redis/dump.rdb ]] && cp /var/lib/redis/dump.rdb "$BACKUP_DIR/redis-$STAMP.rdb"
  fi
  cp "$ENV_FILE" "$BACKUP_DIR/env-$STAMP"
  chmod 600 "$BACKUP_DIR/env-$STAMP"
  [[ -d "$APP/private_media" ]] && tar -czf "$BACKUP_DIR/media-$STAMP.tgz" -C "$APP" private_media
  cp -a /etc/redis/redis-razavi-*.conf "$BACKUP_DIR/" 2>/dev/null || true
  cp /etc/nginx/sites-available/razavi-lms.conf "$BACKUP_DIR/" 2>/dev/null || true
  # نگهداری ۳۰ نسخه‌ی آخر
  ls -t "$BACKUP_DIR"/db-*.dump 2>/dev/null | tail -n +31 | xargs -r rm -f
}

unpack_release() {
  local zip="$1" dir="$RELEASES/$STAMP"
  [[ -f "$zip" ]] || die "فایل zip پیدا نشد: $zip"
  log "باز کردن نسخه‌ی جدید در $dir"
  mkdir -p "$dir"
  unzip -q "$zip" -d "$dir.tmp"
  # اگر zip یک پوشه‌ی ریشه داشت، محتوای آن را بردار
  local src="$dir.tmp"
  [[ -d "$src/razavi_lms" ]] && src="$src/razavi_lms"
  shopt -s dotglob; mv "$src"/* "$dir"/; shopt -u dotglob
  rm -rf "$dir.tmp"
  # تنظیمات محیط (.env) از روی سرور کپی می‌شود؛ هرگز از zip نمی‌آید
  cp "$ENV_FILE" "$dir/.env" && chmod 600 "$dir/.env" && chown "$APP_USER:$APP_USER" "$dir/.env"
  # فایل‌های حساس هرگز از zip نمی‌آیند؛ از نسخه‌ی فعلی منتقل می‌شوند
  for f in private_media; do
    [[ -e "$APP/$f" && ! -e "$dir/$f" ]] && cp -a "$APP/$f" "$dir/$f"
  done
  chown -R "$APP_USER:$APP_USER" "$dir"
  echo "$dir"
}

python_deps() {
  local dir="$1"
  if [[ ! -x "$VENV/bin/python" ]]; then
    log "ساخت محیط پایتون"
    sudo -u "$APP_USER" python3 -m venv "$VENV"
  fi
  if ! cmp -s "$dir/requirements.txt" "$APP/requirements.txt" 2>/dev/null; then
    log "نصب/به‌روزرسانی وابستگی‌ها"
    sudo -u "$APP_USER" "$VENV/bin/pip" install --upgrade pip wheel >/dev/null
    sudo -u "$APP_USER" "$VENV/bin/pip" install -r "$dir/requirements.txt"
  else
    log "وابستگی‌ها تغییری نکرده‌اند"
  fi
}

django_steps() {
  local dir="$1"
  cd "$dir"
  log "بررسی تنظیمات امنیتی"
  sudo -u "$APP_USER" "$VENV/bin/python" manage.py check --deploy || die "بررسی امنیتی ناموفق بود."
  log "جمع‌آوری فایل‌های استاتیک (فونت‌ها، CSS و JS)"
  sudo -u "$APP_USER" "$VENV/bin/python" manage.py collectstatic --noinput >/dev/null
  log "اعمال تغییرهای جدول‌ها (داده‌های موجود حفظ می‌شوند)"
  sudo -u "$APP_USER" "$VENV/bin/python" manage.py migrate --noinput
  sync_schema "$dir"
}

# ستون‌ها و جدول‌هایی که در مدل‌ها هستند اما در دیتابیس نیستند را می‌سازد (فقط اضافه می‌کند؛ هیچ داده‌ای حذف نمی‌شود).
# با کاربر برنامه وصل می‌شود تا مالکیت جدول‌های جدید درست باشد. هر بار نصب یا به‌روزرسانی اجرا می‌شود.
sync_schema() {
  local dir="$1"
  log "همگام‌سازی ستون‌ها و جدول‌های ناموجود در دیتابیس"
  set -a; . /etc/razavi-lms/.env; set +a
  if ! ( cd "$dir" && "$VENV/bin/python" deploy/sync_missing_columns.py --apply > /tmp/razavi-sync.log 2>&1 ); then
    cat /tmp/razavi-sync.log >&2
    die "همگام‌سازی ستون‌ها ناموفق بود؛ خروجی بالا را بررسی کنید."
  fi
  if grep -q "ناموفق: [1-9]" /tmp/razavi-sync.log; then
    cat /tmp/razavi-sync.log >&2
    die "برخی ستون‌ها ساخته نشدند؛ خروجی بالا را بررسی کنید."
  fi
  tail -n 3 /tmp/razavi-sync.log >&2
}

switch_release() {
  local dir="$1"
  local prev=""
  [[ -L "$APP" ]] && prev="$(readlink -f "$APP")"
  # نصب قدیمی: پوشه‌ی واقعی app به نام نگهداری کنار می‌رود (داده‌ها در دیتابیس و private_media کپی‌شده‌اند)
  if [[ -d "$APP" && ! -L "$APP" ]]; then
    mv "$APP" "$BASE/app-old-$STAMP"
    log "نسخه‌ی قدیمی به $BASE/app-old-$STAMP منتقل شد."
  fi
  ln -sfn "$dir" "$APP.new" && mv -T "$APP.new" "$APP"
  echo "$prev" > "$BASE/previous-release" 2>/dev/null || true
  log "نسخه‌ی فعال: $dir"
}

restart_and_check() {
  log "راه‌اندازی مجدد سرویس"
  systemctl restart "$SERVICE"
  sleep 4
  if ! systemctl is-active --quiet "$SERVICE"; then
    journalctl -u "$SERVICE" -n 40 --no-pager
    return 1
  fi
  if ! curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:8000/ 2>/dev/null \
     && [[ ! -S /run/gunicorn.sock ]]; then
    log "هشدار: پاسخ HTTP داخلی دریافت نشد؛ nginx و سوکت را بررسی کنید."
  fi
  return 0
}

prune_releases() {
  ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +$((KEEP_RELEASES+1)) | xargs -r rm -rf
}

rollback_code() {
  [[ -f "$BASE/previous-release" ]] || die "نسخه‌ی قبلی ثبت نشده است."
  local prev; prev="$(cat "$BASE/previous-release")"
  [[ -d "$prev" ]] || die "پوشه‌ی نسخه‌ی قبلی پیدا نشد: $prev"
  log "بازگشت به $prev (داده‌های دیتابیس دست‌نخورده می‌مانند)"
  ln -sfn "$prev" "$APP.new" && mv -T "$APP.new" "$APP"
  restart_and_check || die "سرویس بعد از بازگشت بالا نیامد؛ گزارش را بررسی کنید."
  log "بازگشت کامل شد. اگر migration جدیدی اجرا شده بود، بازیابی دیتابیس از بکاپ لازم است."
}

install_worker_units() {
  # صف کارها (ساخت خودکار اتاق جلسه‌ی آنلاین، ارسال پیامک، پردازش‌ها) و زمان‌بند دوره‌ای
  local envf="/etc/razavi-lms/.env"
  cat > /etc/systemd/system/razavi-lms-worker.service <<EOF
[Unit]
Description=Razavi LMS task worker (Celery)
After=network-online.target postgresql.service redis-razavi-cache.service redis-razavi-broker.service razavi-lms.service
Wants=redis-razavi-cache.service redis-razavi-broker.service

[Service]
User=$APP_USER
WorkingDirectory=$APP
EnvironmentFile=$envf
ExecStart=$VENV/bin/celery -A config worker -Q default,grading -l info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/razavi-lms-beat.service <<EOF
[Unit]
Description=Razavi LMS scheduler (Celery beat) - run exactly one instance
After=network-online.target redis-razavi-broker.service
Wants=redis-razavi-broker.service

[Service]
User=$APP_USER
WorkingDirectory=$APP
EnvironmentFile=$envf
ExecStart=$VENV/bin/celery -A config beat -l info --schedule $BASE/celerybeat-schedule
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  for s in razavi-lms-worker razavi-lms-beat; do
    systemctl enable "$s" >/dev/null 2>&1 && systemctl restart "$s" && log "فعال و در حال اجرا: $s"
  done
}

install_service_units() {
  log "ثبت سرویس‌ها برای راه‌اندازی خودکار با ریست سرور"
  mkdir -p /etc/systemd/system/razavi-lms.service.d
  cat > /etc/systemd/system/razavi-lms.service.d/boot-order.conf <<'EOF'
# برنامه فقط بعد از دیتابیس، Redis و شبکه بالا می‌آید و خودکار راه‌اندازی می‌شود
[Unit]
After=network-online.target postgresql.service redis-razavi-cache.service redis-razavi-broker.service
Wants=network-online.target redis-razavi-cache.service redis-razavi-broker.service
Requires=postgresql.service

[Service]
Restart=on-failure
RestartSec=5
EOF
  systemctl daemon-reload
  install_worker_units
  for s in postgresql nginx redis-razavi-cache redis-razavi-broker "$SERVICE"; do
    systemctl enable "$s" >/dev/null 2>&1 && log "فعال شد برای راه‌اندازی خودکار: $s" || log "هشدار: $s فعال نشد (وجود دارد؟)"
  done
}

cmd_install() {
  need_root; check_env
  install_packages; ensure_user_and_dirs; enable_boot_services
  local dir; dir="$(unpack_release "$1")"
  python_deps "$dir"
  django_steps "$dir"
  switch_release "$dir"
  install_service_units
  restart_and_check || die "سرویس بالا نیامد."
  log "نصب کامل شد."
}

cmd_update() {
  need_root; check_env
  [[ -e "$APP" ]] || die "نصب اولیه انجام نشده است؛ ابتدا install را اجرا کنید."
  enable_boot_services
  backup_all
  local dir; dir="$(unpack_release "$1")"
  python_deps "$dir"
  if ! django_steps "$dir"; then
    log "خطا در مرحله‌ی Django؛ نسخه‌ی قبلی فعال می‌ماند."
    rm -rf "$dir"; exit 1
  fi
  switch_release "$dir"
  if ! restart_and_check; then
    log "سرویس با نسخه‌ی جدید بالا نیامد؛ بازگشت خودکار به نسخه‌ی قبلی."
    rollback_code || true
    exit 1
  fi
  prune_releases
  log "به‌روزرسانی کامل شد. داده‌های PostgreSQL و Redis حفظ شدند."
}

cmd_rollback() { need_root; rollback_code; }

cmd_status() {
  systemctl --no-pager status postgresql redis-server nginx "$SERVICE" | head -60
  echo; echo "نسخه‌ی فعال: $(readlink -f "$APP" 2>/dev/null || echo -)"
}

cmd_boot_setup() { need_root; enable_boot_services; install_service_units; log "راه‌اندازی خودکار فعال است."; }

case "${1:-}" in
  install)    cmd_install "${2:?فایل zip را وارد کنید}" ;;
  update)     cmd_update  "${2:?فایل zip را وارد کنید}" ;;
  rollback)   cmd_rollback ;;
  status)     cmd_status ;;
  boot-setup) cmd_boot_setup ;;
  *) echo "استفاده: $0 {install|update|rollback|status|boot-setup} [zip]"; exit 2 ;;
esac
