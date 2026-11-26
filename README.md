<<<<<<< HEAD
# Razavi LMS — Django/DRF core (v0.1)

Implements the backend foundation from the deployment design: org-scoped RBAC (province → region → school),
JWT auth with lockout + throttling, courses/enrollment, online exams (idempotent answers, server-side deadlines,
auto-submit), live-class join URLs (BigBlueButton adapter), audit log, health checks, Celery, Docker.

## First run
    pip install -r requirements.txt
    python manage.py makemigrations accounts core courses exams liveclass   # migrations are not pre-generated
    python manage.py migrate
    python manage.py createsuperuser
    DJANGO_DEBUG=1 python manage.py runserver     # dev only; production uses gunicorn
    python manage.py test
    DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=... python manage.py check --deploy

## Teacher authoring & reports (v0.2)
- `POST /api/courses/{id}/exams/` create exam with nested questions (2–6 choices, exactly one correct, ≤200 questions)
- `GET|PATCH /api/exams/{id}/manage/`, `PUT /api/exams/{id}/questions/`, `POST /api/exams/{id}/publish/`
  Questions and timing are locked once any student has an attempt (409); unpublished exams are invisible to students.
- `GET /api/reports/exams/{id}/` (counts, avg/min/max, 10% histogram, per-question correct rate; cached 60 s)
- `GET /api/reports/exams/{id}/results.csv` (streamed, UTF-8 BOM for Excel, formula-injection safe)
- `GET /api/reports/orgs/{id}/` province→regions / region→schools rollup (admins only, in-scope only; cached 5 min)
At province scale replace the live rollup with nightly Celery-built snapshot tables or the Standby/warehouse.

## Admin panel (v0.3)
Role-scoped Django admin (`/admin/`). Menu groups: organisation & users, academic structure, courses & content,
exams, live classes, integrations. Every request is checked server-side:
- Super admin/superuser: everything. Province/county/region/school admins: only their own org subtree, and only the
  models their role may write (e.g. region admins create schools, not regions). Teachers: only their own courses'
  content, exams, questions, sessions and results.
- Users: an admin can only create/edit users of a strictly lower rank inside their scope; staff access follows the role.
- Hierarchy is enforced (province → county → region → school); moving a unit rewrites its subtree paths.
- Reference data (years, levels, grades, fields, subjects) is read-only for non-super admins: `py manage.py seed_reference`.
- Integrations (BBB/Adobe/Skyroom/SMS/SSO): super admins only; secrets are never stored in the database, only the NAME of an
  environment variable; the connection check refuses loopback/link-local targets and does not follow redirects.
- Course content: URLs must be https (no `javascript:`), files stay in object storage/CDN.
- Questions/timing are locked once a student has started an exam.

## v0.4 additions
- **Academic year** on courses and enrolments (history is kept: a new year = new courses/enrolments).
- **Question types**: mcq, true/false, essay (manual grading); per-question points; `shuffle_questions`, `shuffle_choices`,
  `draw_count` (random subset per student, deterministic per student so reloads never change it); **question bank**
  (`/api/bank/`, `/api/exams/{id}/add-from-bank/` copies, never links).
- **Assignments** (`/api/assignments/…`): publish, submit (text and/or https file link, late rule), grade.
- **Grade history**: every essay/assignment grade is appended to `GradeChange`; changing a grade requires a reason.
- **Attendance** (`/api/attendance/`, `/api/me/attendance/`): daily/period/course slots, statuses (present, absent, late,
  excused, leave); first entry free, corrections need a reason and are logged (`AttendanceChange`); read-only in admin.
- **Parents** (`parent` role): `/api/children/…`, only students linked AND verified by staff (`ParentLink`).
- **MFA (TOTP)**: login asks for a code when a device exists; admin roles must have MFA in production
  (`MFA_ENFORCE`, default on outside DEBUG); admin site login also asks for the code; bootstrap with
  `py manage.py mfa_enroll <username>`. Needs `MFA_ENCRYPTION_KEY` (Fernet key) in production.
- **Live-class automation**: `auto_create` sessions are created at the provider by a Celery task (idempotent id, retries with
  backoff, `failed` state); signed webhook `POST /api/hooks/<system_id>/` (HMAC + timestamp + dedupe) feeds presence;
  `finalize_ended_sessions` turns presence into attendance and NEVER invents data when the provider sent no events.
  The BBB payload mapping must be verified against your BBB version.
- **Province reports**: `DailyOrgStats` built nightly (`build_daily_stats`); `/api/reports/orgs/{id}/stats/` reads it.
- **Teacher interface** (the web page at `/`, for teachers and admin roles): create/edit/publish exams with question
  builder (mcq, true/false, essay, shuffling, random draw), add questions from the bank, grade essays (reason required
  to change a grade), exam results + CSV; assignments with submissions and grading; attendance entry (daily/course/period,
  corrections need a reason); question bank; live classes (create session, join, attendance status); course content
  management. Extra APIs: `/api/classrooms/`, `/api/classrooms/{id}/students/`, `POST /api/courses/{id}/content/`,
  `/api/content/{id}/`, `/api/live/`, `POST /api/courses/{id}/sessions/`.
- **Calendar**: dates are stored in Gregorian and shown in BOTH calendars (Jalali + Gregorian). The web app uses a dual
  date picker (type Jalali or Gregorian; the other is shown live); lists/admin show both; CSV has a Jalali column.
  `apps/core/jalali.py` (Python) and the JS port are dependency-free and tested round-trip for 2000-2060.
- **Weekly timetable** (`apps.timetable`): school bell schedule + per-class weekly grid. School admins and above manage it
  (inside their scope); teachers and students see their own program (`GET /api/schedule/`). Rules: no overlapping bells,
  a teacher cannot be in two classes in the same period, a period used by a timetable cannot be deleted.
- **Academic calendar** (`apps.schoolcal`): holidays / closures / exam periods / events scoped to an org unit and everything
  below it (nationwide = super admin only). Events with `blocks_attendance` close MANUAL attendance entry for those days
  (online-provider attendance is not blocked: classes may run online on a closure day). `py manage.py seed_holidays --year 1405`
  creates the FIXED solar holidays; lunar holidays must be entered from the official calendar.
- **Grade appeals** (`apps.appeals`): a student appeals a graded essay answer, assignment or whole exam within
  `APPEAL_WINDOW_DAYS` (default 7); the course teacher decides; a rejected appeal can be escalated once to a school admin.
  Accepting changes the grade through the normal path and writes the grade history; every step is logged (`AppealEvent`).
- **Transfers** (`apps.transfers`): between schools (source releases, destination accepts, destination executes) or between
  classes of one school (one step). Grades, attendance, attempts and submissions are kept; only the current school-year
  class/course membership moves, and parent links must be re-verified by the new school. One active transfer per student.
- **Multi-school staff**: a principal (`school_admin`) or teacher works in every school listed in `SchoolMembership`
  (set by a school-admin inside their scope, or automatically from the home school). Scope helpers live in
  `apps/core/scope.py` (`scope_paths`, `scope_q`, `org_in_scope`); students still belong to exactly one school.
  `GET /api/me/schools/` lists the kartabl schools.
- **Bulk import** (`apps.imports`): students, classes, teachers and courses from CSV, XLSX or pasted Seyda tables.
  Preview: green = valid, red = error with reason (duplicates in file and in the database, national-ID checksum,
  unknown class/grade/field). Cells can be corrected and re-validated before `commit`, which writes only valid rows
  (each in its own savepoint). Imported users get no usable password; an admin sets a temporary one
  (`POST /api/users/<id>/reset-password/`, shown once). Names are normalised (ي/ك→ی/ک, Persian digits, tel links removed).
- **Kartabl (active school)**: the top bar switches the school; every request carries `X-School-Id`, and list views
  (courses, exams, classes, live sessions, assignments) filter by it. The id is re-checked against the user's scope.
- **Region default access**: province/county/region admins act on every school below their unit automatically;
  principals and teachers act on the schools in their memberships.
- **School profile** (`/api/schools/<id>/profile/`): school code (unique), level (one per school), type, shift,
  ownership, gender, country, province → county → region (cascade), postal code, address, phone, fax, website, email.
  Commands: `seed_catalog` (levels, grades 1–12, four upper-secondary fields), `import_fields fields.csv`,
  `seed_provinces` (31 provinces), `import_geo counties_regions.csv` (counties and regions with codes).
- **Girls/boys rule**: upper-secondary, vocational and technical schools with a boys/girls profile reject students of the
  other gender (import, admin save, transfers). Primary and lower-secondary schools may be mixed.
- **Photos**: JPG/PNG re-encoded to 256 px JPEG without metadata, stored privately, served only through
  `/api/users/<id>/photo/` (self, admins in scope, the student's teachers, verified parents). Bulk zip upload matches
  entries by student code or national id inside the chosen school and reports unmatched/invalid files.
- **Address**: province → county cascade in the school profile; the region (ناحیه/منطقه) is chosen separately.
  Counties and regions are editable by admins inside their province (`/api/geo/counties/`, `/api/geo/regions/`).
  `seed_counties` writes the counties of Khorasan Razavi from memory — VERIFY them against the official list;
  other provinces' counties come from `import_geo` (CSV).
- **Seyda / Excel import** (`apps.imports`): kinds students, classes, teachers, course links and the curriculum catalogue
  (`subjects`, super admin only). Headers from the Seyda samples are recognised; title rows above the header are skipped.
  Paste a browser/Excel copy (tab separated). A whole table copied as ONE line cannot be split reliably: export to Excel.
  Class fields are matched by study-field CODE (e.g. 14 = دوره متوسطه اول, 15000 = ریاضی فیزیک).
- **Shared subjects**: a curriculum subject (`academics.Subject`) belongs to several grades (`grades`) and several
  study fields (`study_fields`). A field must match the level of every grade it is linked with. Admins choose both lists
  in the subject form (validated on save); the Seyda/Excel import accepts several grades and field codes in one cell,
  separated by `،` `,` `/` `;` (e.g. `دهم، یازدهم، دوازدهم` and `15000,15001`). A class course is valid only when the subject
  is offered in the class's grade.
- **Filters**: the «درس‌ها» page filters by grade and study field (`/api/courses/?grade=&field=`); a course matches a grade
  through its class or its curriculum subject. Staff also see the curriculum catalogue for the same filter
  (`GET /api/subjects/?grade=&field=&q=`).
- **SMS and password recovery** (`apps.sms`): each school connects its own SMS panel (HTTPS URL, auth header, JSON body
  template with {to} {sender} {message}). The key is stored encrypted (`SMS_ENCRYPTION_KEY`) and never returned by the API.
  Students, teachers and parents recover their password with a 6-digit code sent to the mobile on file
  (`/api/recovery/request/`, `/api/recovery/verify/`): the answer is the same whether the account exists, codes are
  stored hashed, expire in 5 minutes, allow 5 tries, one per 60 s and 5 per hour; a successful reset revokes old sessions.
  Panel test: `POST /api/schools/<id>/sms/<gid>/test/`; log without message text: `/api/schools/<id>/sms-log/`.
  The provider's own URL and JSON body must be entered from its documentation; this build does not hard-code any provider.
- **System administrator panel** (`/system/`): a separate panel from the Django admin, for the super admin only.
  Sections: users, memberships, parent links, provinces/counties/regions/schools, levels/grades/fields/subjects,
  academic years, classes, course links, external systems. Each resource is a whitelist in
  `apps/sysadmin/resources.py` (passwords and flags are never writable), validated with the model's own rules
  (`full_clean`) and the subject link rule; deletes that would break data return 409. Users get a temporary password
  from the form (shown once). API: `/api/system/schema/`, `/api/system/<resource>/`, `/api/system/options/`.
  The Django admin stays for technical maintenance only.
- **System panel covers every module** (`/system/`): dashboard counts; structure (provinces to schools, profiles,
  academic years, levels, grades, fields, subjects, classes, course links); people (users, memberships, parent links,
  experts); courses, enrolments, assignments, live sessions; exams, attempts, appeals, grade history; attendance,
  timetable, calendar, transfers; SMS panels and logs, audit log, two-factor devices; external systems, import jobs,
  files, provider events, daily statistics. Read-only modules cannot be changed from the panel.
  Limits: SMS secrets are set only through the school SMS API, and questions/bank items are edited by teachers.
- **Theme**: system (follows the device's light/dark setting), light or dark; chosen in the menu and remembered in the browser.
- **Academic evaluation** (`apps.evaluation`): each course average = mean of its exam and graded-assignment percentages;
  overall = mean of course averages; pass = at or above the pass mark; band labels (عالی/خوب/متوسط/ضعیف) by the level's policy.
  Policies are set per level (school, region, county, province; the national default by the super admin) and apply to the
  most specific level covering a school. Reports: `evaluation_students`, `evaluation_classes` (school), `evaluation_schools`
  (district). Students see their own evaluation (`/api/me/evaluation/`).
- **Final grade formula**: course grade = (continuous × continuous weight + final × final weight) ÷ (sum of the two weights).
  Continuous = average of exams marked «مستمر» and graded assignments; final = average of exams marked «پایانی».
  Weights are set per level in the evaluation policy (default 1 and 1). A course without a final exam is graded on its
  continuous marks; a course whose final exam has no marks yet is pending and does not enter the average.
- **Student ranking** (`apps/evaluation/ranking.py`): for each student, rank in the class, in the school, in the region
  and in the province, overall (معدل) and for each course (`ranking_overall`, `ranking_course`, `ranking_district` reports).
  Ties share a rank (1, 2, 2, 4). Province tables are cached for 10 minutes; for a very large province precompute them nightly.
- **Student school history** (`StudentSchoolHistory`): every school period is kept (school, from date, to date, reason,
  transfer). A completed transfer closes the old period and opens the new one; a bulk import opens the first period.
  `GET /api/students/<id>/history/` shows the whole trail to the student, verified parents, admins of any school in the
  trail (so the new school sees the earlier periods) and teachers of the student. Each old period shows attendance and
  the courses with exams in that period. Records of the old school stay attached to the old school.
- **Design and menus**: one organisational look (top bar with the organisation identity, grouped sidebar, cards,
  light/dark/system theme). Every role gets its own grouped menu: students (teaching, attendance, results),
  teachers (teaching, attendance, reports), principals and regional admins (+ management, imports, transfers),
  experts (read-only schools, courses, exams, reports) and parents. The super admin also has `/system/`.
- **No Django admin needed**: `/system/` (super admin) manages every module and has a «ابزارهای سیستم» page (current academic year,
  base data, authenticator enrol/reset, account unlock, SMS panel key, external-system test, system status).
  Other admins use «مدیریت اطلاعات» in the main app: users, classes, courses, enrolments, memberships, parent links,
  school profiles and (regional admins and above) schools — always limited to their own scope and below their own rank;
  users are deactivated, never deleted. Set `DJANGO_ADMIN_ENABLED=0` to remove the Django admin route entirely.
- **Two-factor activation shows a QR code** (generated in the browser by a small built-in encoder, nothing external) in the
  profile page, in the forced activation screen and in the system tools; the manual key stays as a fallback.
- **Forms depend on the role** (system panel and «مدیریت اطلاعات»): a principal needs a region or school plus a level; an expert needs
  kind (curriculum/assessment), level and a province/county/region; other roles hide those fields and the server clears them.
  Every foreign key is a filtered, searchable picker (province → region → school; students only where a student is meant; schools only
  where a school is meant). Academic years are defined by two dates (name 1405-1406 is automatic). Bell periods use time inputs.
- **Reports with full filtering**: `directory` (every school with its profile) and `summary` (grouped by region/county/province/level) with filters
  province, county, region, level, type, shift, ownership, gender and text; district exam report; school exam report.
- **Content**: no URL type any more; text content uses a rich editor (font, size, bold, italic, underline, colour, lists, image, audio);
  the HTML is sanitised on every save (`apps/core/htmlclean.py`) and embedded files are checked per student.
- **Catalogue CSV import** (counties, regions, fields) for the super admin and province admins; **province-level zip** updates student photos and
  details by national id for admins and experts inside their unit.
- Counties: `seed_counties` now contains only certain Khorasan Razavi counties (Tabas belongs to South Khorasan). Complete the rest with the CSV import.
- **Assignments**: the teacher writes the task in the rich editor, attaches files (any allowed type) or records voice,
  chooses all students of the course or selected ones, and sets the opening and due times. Students read the task with its
  attachments, answer with text, files or voice; submissions are visible only to the course teacher.
- **Calendar**: events are edited in place (click an event); holidays and closures are set on the day.
- **Bell periods**: a school's bell times (hour:minute) are edited by school managers and the system administrator; choose
  province → region → school.
- **Seyda import**: a guide lists the order (classes → fields/subjects → teachers → students → course links); any copy-paste layout
  is accepted (tab, several spaces, comma, semicolon, pipe). Errors are returned in Persian with the reason.
- **Required fields** are checked before saving in every system form, and the missing ones are named.
- **Structure**: level → schools → fields → grades → classes → courses and students. A school belongs to ONE level; its classes
  use only grades of that level; its pickers offer only its own level's grades, fields and subjects (so a vocational school sees
  vocational subjects only). A school is placed in ONE county and ONE region: non-super admins see only that county and region.
- **Gender**: a boys-only or girls-only school fixes the gender of its students automatically (no entry needed); mixed schools
  keep the gender entry, and upper-secondary, vocational and technical schools must be single-gender or enter it.
- **School code** is unique and shown in the edit form.
- **Missing classes from a student list**: the preview lists the classes the rows need (grade + class number + field) that the
  school does not have. On confirmation (checkbox, default on) they are created as «پایه کد» (e.g. «دوم 3») with that code and
  field, then the students are committed. Nothing is created without that confirmation.
- **Exam control**: start and end date-time can be changed; an exam can be put in suspension (students cannot start or
  continue, answers are refused with 423, times are kept) and taken out of it. Teachers, the question designer and school admins do this.
- **Participation report** (per exam, CSV too): completed, interrupted (line dropped in the middle: no answer for three minutes
  or time ran out without a final submit), in progress, not attended. An interrupted student can be sent back into the exam
  («برگرداندن به آزمون»): a fresh window of the exam's duration (never past the exam's end) and the saved answers stay.
- **Exam instructions and results**: the designer writes the instructions (rich text) shown before the student starts («شروع آزمون»
  after reading them). After the submit the student sees what the designer chose: nothing, the total score, the count of fully
  correct / wrong / partial answers, or both. Question order and option order are randomised per student when the designer ticks them.
- **Activity log** («گزارش فعالیت کاربران»): logins and failed logins, task and exam views (once per object per ten minutes),
  exam starts and submits, result views, task submissions — with user, role, school, time and address. The system administrator sees
  everyone; other administrators see users inside their scope. Filters: name / username / national id, activity, unit, date range.
- **SMS to parents** (`apps.sms.notify`): verified parents with a mobile receive short messages through the school's own panel
  for: absence or lateness (written when the attendance is recorded), a submitted homework, a homework not submitted after its
  due time, an exam taken fully, an exam started but not completed (connection lost), and an exam not taken at all. Each event
  is queued once per parent (dedupe key); the outbox is sent every five minutes by Celery beat and retried up to five times.
  A parent can be switched off per link (`notify_sms`). Messages contain no grades. The system administrator sees the outbox
  («صف پیامک والدین»); without an active panel for the school, the messages are marked as skipped.
- **Monitor** («پایش CPU، RAM و کندی», system panel): CPU, RAM, swap, disk, process memory, database ping, Redis cache (hit rate,
  memory, clients), Celery queue lengths, requests per minute with average time, and the recent slow requests (above
  `SLOW_REQUEST_MS`, default 800 ms). Requires `psutil` (in requirements). Refreshes every ten seconds.
- **Calendar and occasions**: `seed_occasions` loads the official holidays and occasions of 1405 and 1406 (Jalali date plus
  computed Gregorian date, categories holiday / national / religious, colours red / blue / green / purple, school events in purple).
  Entries whose wording in the source conflicts with the calendar (for example «نوروز و عید فطر» on 1 Farvardin 1405) are kept
  and marked in their description; check them against the official calendar. The system administrator edits the occasions in the system panel.
- **Date picker**: every date and date-time field (exams, assignments) opens a Jalali month grid; no typing. Times are chosen in a time field.
- **School deputies** (`school_deputy_edu` معاون آموزشی and `school_deputy_exec` معاون اجرایی): they work inside their school(s)
  of one county, like principals, but are not administrators. The educational deputy sees teacher attendance and manages
  timetable and bells of his school. The executive deputy sees and records grades (essay marks) and grade reports of his school.
  Discipline records, process book, teacher evaluation, worksheets, meetings, and the cultural deputy are not built yet.
- **Disciplinary score** («نمره انضباطی»): factors (عامل) with points (− deduction, + addition). Defaults (`seed_discipline_defaults`):
  late −0.5, unexcused absence −1, excused absence −0.25, cleanliness −0.5, unusual answering to staff −1, classroom misconduct −0.75,
  missing materials −0.25, good conduct +0.5, helping others +0.25. Teachers, principals and both deputies define new factors for their
  school; the system administrator changes the defaults. A discipline book entry copies the factor's title and points, so later changes
  do not rewrite history. Score = 20 + total of entries, kept between 0 and 20. The worksheet («کاربرگ انضباطی») is printed from the page.
- **Class marks and class worksheet**: a teacher gives a class mark («نمره سرکلاسی») for many students of his course at once
  (title, date, maximum, one score per student; the server refuses scores above the maximum and students outside the course).
  The class worksheet («کاربرگ نمرات کلاس») shows, for every student, the mark of each course (exams, homework and class marks together,
  in %), the overall average and the rank in the class (equal averages share the rank). It can be printed.
- **Cultural deputy** (`school_deputy_culture` معاون پرورشی; the principal also manages these): competitions and festivals inside the
  school (مسابقه، جشنواره) with the top five places (رتبه ۱ تا ۵): the person or group and the score are typed by hand. Results
  are shown to students only when the deputy publishes them. Trips (اردو) with the participants and their consent (در انتظار،
  رضایت داده، رضایت نداده, with the guardian's name); the consent form (رضایت‌نامه) is printed for each student.
- **Meetings** («جلسات مدرسه»; managed by the principal and both deputies): teachers' council, school council, a meeting with parents
  and educators, or another meeting, with start and end date-time, place and agenda. Invitations go to the people chosen (staff of the
  school, and for parents' meetings and school councils the verified parents); each invited person receives an SMS through the school's
  panel (queued once) and an invitation letter that can be printed or saved as PDF. Minutes (صورت‌جلسه) use the rich editor and are
  cleaned on save. Decisions (مصوبات) have a follow-up person from the school's staff, a due date and a done flag; teachers see the
  decisions they follow up in «جلسه‌های من».
- **Parent panel**: for each verified child the parent sees the attendance, homework, exam results (as before), the disciplinary
  score and book with a printable worksheet, the grades per course (exams, homework and class marks in %), the trips with the
  consent form (print / PDF), the festivals and competitions with the consent form and the top places once published, and the
  meetings he is invited to («جلسه‌های من (اولیا)»). The cultural deputy records each festival participant and the guardian's consent.
- **Process book** («دفتر فرایندی»): the educational deputy defines processes for his school (title, description, a sensitive flag,
  and the questions: text, number, date, yes/no, or choice; required or optional). The teacher and the educational deputy record
  entries for a student; each entry is checked against its questions. The student's book shows every process with its entries, the
  attendance counts, the academic summary (average and weak courses below 50%) and the discipline score. Sensitive processes (for example
  health) are shown only to the educational deputy and the principal. The educational deputy also evaluates teachers: five criteria
  from 1 to 5, the average computed on save.
- Run **one** `celery beat` and workers with `-Q default,grading`.

## Safety measures built in
- No secrets in code; production refuses to start without SECRET_KEY / ALLOWED_HOSTS; DEBUG off by default
- HTTPS redirect, HSTS, secure cookies, nosniff, frame deny; obscure admin URL (also restrict to VPN)
- Short-lived JWT (10 min) + rotating, blacklisted refresh; per-IP throttle and per-account lockout on login
- Every query on org data goes through `org_scope_q`; out-of-scope objects return 404
- Exam correctness flags never serialised; choice/question/exam consistency checked on every answer
- Unique constraints make start/answer/enroll idempotent under retries and races
- Generic 500 responses with request id (no stack traces); append-only audit log
- Files/video never pass through Django; only signed provider join URLs

## Speed measures built in
- One DB query to authenticate (user+org); PgBouncer-ready (no server-side cursors, CONN_MAX_AGE=0)
- Single-statement upsert for answers; grading is one COUNT; questions cached in Redis per exam version
- Bounded pagination, GZip, indexed scope/FK paths (`varchar_pattern_ops` on org path)
- Celery queues separated; periodic auto-submit uses `skip_locked`

## Not included yet (needs decisions/work)
Load-test results (run the plan in the deployment doc), SSO integration, Adobe Connect/Skyroom adapters,
notifications/SMS, file upload via
object storage, front-end. Treat capacity numbers as unverified until load-tested. The code was not executed in
the authoring sandbox (Django could not be installed there): run the test suite first and fix any environment issues.
=======
# lms
>>>>>>> 685605f60698c89d44e6b2c98958eadf989d4975
