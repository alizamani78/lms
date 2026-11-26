# راه‌اندازی سرور Ubuntu برای سامانه آموزش و پرورش خراسان رضوی

> این راهنما مراحل را دستی و قدم‌به‌قدم می‌آورد. فرض: Ubuntu 22.04 یا 24.04، دسترسی `sudo`، دامنه‌ای که به IP سرور اشاره می‌کند (مثلاً `lms.example.ir`)، و کد برنامه در `/opt/razavi-lms/app`.

## ۱. بسته‌های پایه
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-dev build-essential libpq-dev \
  postgresql postgresql-contrib pgbouncer redis-server nginx ufw cron curl openssl
sudo systemctl disable --now redis-server   # سرویس پیش‌فرض را خاموش کنید؛ دو نمونه‌ی مخصوص برنامه ساخته می‌شود
```

## ۲. PostgreSQL
```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE razavi LOGIN PASSWORD 'رمز-قوی';
CREATE DATABASE lms OWNER razavi;
\c lms
GRANT ALL ON SCHEMA public TO razavi;
SQL
PGPASSWORD='رمز-قوی' psql -h 127.0.0.1 -U razavi -d lms -c 'SELECT 1;'
```

## ۳. PgBouncer (اختیاری ولی توصیه‌شده)
فایل `/etc/pgbouncer/pgbouncer.ini` را با بخش `[databases]` که `lms` را به `127.0.0.1:5432` وصل می‌کند بسازید و در `/etc/pgbouncer/userlist.txt` بنویسید:
```
"razavi" "رمز-قوی"
```
```bash
sudo chown postgres:postgres /etc/pgbouncer/userlist.txt && sudo chmod 640 /etc/pgbouncer/userlist.txt
sudo systemctl enable --now pgbouncer
```
در تنظیمات برنامه `DB_PORT=6432` بگذارید؛ مهاجرت‌ها را با `DB_PORT=5432` اجرا کنید.

## ۴. Redis (دو نمونه: کش و صف)
- نمونه‌ی کش: پورت `6379`، بدون ذخیره‌سازی دائمی، سیاست `allkeys-lru`.
- نمونه‌ی صف: پورت `6380`، با AOF.
هر دو با `requirepass` محافظت شوند. سرویس‌های systemd به نام `redis-razavi-cache` و `redis-razavi-broker` بسازید و فعال کنید:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now redis-razavi-cache redis-razavi-broker
redis-cli -p 6379 -a 'رمز-کش' ping    # باید PONG بدهد
```

## ۵. برنامه: محیط پایتون و تنظیمات
```bash
sudo useradd --system --home-dir /opt/razavi-lms --shell /usr/sbin/nologin razavi
sudo mkdir -p /opt/razavi-lms /var/lib/razavi-lms/private /var/log/razavi-lms /etc/razavi-lms
sudo chown -R razavi:razavi /opt/razavi-lms /var/lib/razavi-lms /var/log/razavi-lms
python3 -m venv /opt/razavi-lms/venv
/opt/razavi-lms/venv/bin/pip install -r /opt/razavi-lms/app/requirements.txt
```
فایل `/etc/razavi-lms/env` (دسترسی فقط root و گروه `razavi`):
```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<openssl rand -hex 48>
JWT_SIGNING_KEY=<openssl rand -hex 48>
MFA_ENCRYPTION_KEY=<کلید Fernet>
SMS_ENCRYPTION_KEY=<کلید Fernet>
DJANGO_ALLOWED_HOSTS=lms.example.ir
DJANGO_CSRF_TRUSTED_ORIGINS=https://lms.example.ir
DJANGO_ADMIN_URL=admin-<تصادفی>/
DB_NAME=lms
DB_USER=razavi
DB_PASSWORD=رمز-قوی
DB_HOST=127.0.0.1
DB_PORT=6432
REDIS_CACHE_URL=redis://:رمز-کش@127.0.0.1:6379/1
CELERY_BROKER_URL=redis://:رمز-صف@127.0.0.1:6380/0
PRIVATE_MEDIA_ROOT=/var/lib/razavi-lms/private
SLOW_REQUEST_MS=800
```
کلید Fernet: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

مهاجرت‌ها و داده‌ی پایه:
```bash
cd /opt/razavi-lms/app
set -a; . /etc/razavi-lms/env; set +a; export DB_PORT=5432
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py migrate --noinput
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py collectstatic --noinput
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py seed_catalog
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py seed_provinces
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py seed_counties
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py seed_occasions
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py seed_discipline_defaults
sudo -E -u razavi /opt/razavi-lms/venv/bin/python manage.py check --deploy
```

## ۶. Gunicorn و Celery (systemd)
سرویس‌های `razavi-lms-web`، `razavi-lms-worker` و `razavi-lms-beat` را فعال کنید:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now razavi-lms-web razavi-lms-worker razavi-lms-beat
```
فقط **یک** `beat` باید اجرا باشد. در هر سرویس `Restart=on-failure` بگذارید.

## ۷. nginx و SSL دستی
### الف) Let's Encrypt با certbot (تأیید DNS دستی)
```bash
sudo apt install -y certbot
sudo certbot certonly --manual --preferred-challenges dns -d lms.example.ir -m admin@example.ir --agree-tos
```
رکورد TXT را که certbot نشان می‌دهد در DNS بسازید و پس از چند دقیقه Enter بزنید. گواهی در `/etc/letsencrypt/live/lms.example.ir/` ساخته می‌شود.

### ب) گواهی سازمانی
`fullchain.pem` و `privkey.pem` را در `/etc/ssl/razavi/` بگذارید؛ `chmod 600 /etc/ssl/razavi/privkey.pem`.

### پ) فایل nginx
یک فایل `/etc/nginx/sites-available/razavi-lms` بسازید با این ساختار:
- `server` روی پورت ۸۰ که فقط به HTTPS هدایت می‌کند (و مسیر `/.well-known/acme-challenge/` را نگه می‌دارد).
- `server` روی ۴۴۳ با `ssl_certificate` و `ssl_certificate_key` (مسیرهای بالا)، `Strict-Transport-Security`، `X-Frame-Options`، `X-Content-Type-Options`.
- `client_max_body_size 110m;`
- محدودیت نرخ برای `/api/auth/login/` (حدود ۱۰ در دقیقه) و کل `/api/` (حدود ۶۰ در ثانیه).
- `location /static/` و `location /` که به سوکت Gunicorn پاس می‌دهد.
```bash
sudo ln -sf /etc/nginx/sites-available/razavi-lms /etc/nginx/sites-enabled/razavi-lms
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
```

### ت) تمدید خودکار
```bash
sudo certbot renew --dry-run
echo 'deploy-hook = systemctl reload nginx' | sudo tee -a /etc/letsencrypt/renewal/lms.example.ir.conf
```

## ۷-ث. سیاست امنیتی محتوا (CSP) — رفع خطای «Content-Security-Policy» و «خطا در هنگام ورود»
برنامه خودش CSP مبتنی بر nonce را می‌فرستد. اگر nginx هم هدر `Content-Security-Policy` دیگری بفرستد، مرورگر هر دو را اعمال می‌کند و اسکریپت‌ها و استایل‌های برنامه مسدود می‌شوند (همان خطایی که در کنسول دیده می‌شود: `script-src 'self' 'unsafe-eval'` و `default-src 'self'`).

راه‌حل: هدر CSP را فقط برنامه بفرستد. در بلوک `server` مربوط به `lms.example.ir` این خطوط را بررسی و حذف کنید:
```nginx
# فقط برنامه CSP بفرستد؛ هدر CSP پروکسی را حذف کن
proxy_hide_header Content-Security-Policy;
proxy_hide_header Content-Security-Policy-Report-Only;
```
و هیچ‌جای فایل nginx یا فایل‌های `conf.d` خط `add_header Content-Security-Policy ...` نباشد:
```bash
sudo grep -rn "Content-Security-Policy" /etc/nginx/
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://lms.example.ir/ | grep -i content-security-policy
```
خروجی دستور آخر باید فقط یک هدر CSP با `script-src 'nonce-...'` باشد (از برنامه). اگر دو هدر دیدید، هنوز یکی از فایل‌های nginx یا یک CDN/پروکسی بیرونی آن را اضافه می‌کند.

## ۸. فایروال
```bash
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## ۹. پشتیبان روزانه (cron)
```bash
echo '30 2 * * * razavi /usr/bin/pg_dump -h 127.0.0.1 -U razavi lms | gzip > /var/backups/razavi-lms/lms-$(date +\%F).sql.gz' | sudo tee /etc/cron.d/razavi-backup
sudo mkdir -p /var/backups/razavi-lms && sudo chown razavi:razavi /var/backups/razavi-lms
```
پشتیبان را هفتگی به سرور دیگری کپی کنید.

## ۱۰. اجرای خودکار پس از ریستارت
- سرویس‌های systemd با `enable` پس از ریستارت بالا می‌آیند.
- برای بررسی پس از ریستارت:
```bash
echo '@reboot root sleep 60 && systemctl is-active razavi-lms-web razavi-lms-worker razavi-lms-beat >> /var/log/razavi-lms/boot-check.log 2>&1' | sudo tee /etc/cron.d/razavi-after-reboot
```

## ۱۱. بررسی نهایی
```bash
sudo systemctl list-units --type=service | grep -E 'razavi|redis-razavi'
curl -I https://lms.example.ir/     # باید 200 و HSTS بدهد
```
وضعیت پایش (CPU، RAM، کندی) در پنل مدیر سیستم در دسترس است.

## ۱۲. مشکلات رایج
| نشانه | کار |
|---|---|
| 502 Bad Gateway | `sudo journalctl -u razavi-lms-web -n 100` |
| certbot گواهی نمی‌دهد | DNS یا پورت ۸۰ را بررسی کنید |
| Redis رد می‌کند | رمز در `REDIS_*_URL` با رمز فایل تنظیمات Redis یکی باشد |
| کارهای پس‌زمینه انجام نمی‌شوند | `systemctl status razavi-lms-worker razavi-lms-beat` |
| خطای migrate روی PgBouncer | `DB_PORT=5432` را برای migrate بگذارید |
