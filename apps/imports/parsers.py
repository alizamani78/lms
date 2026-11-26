"""Turns CSV / XLSX / pasted text (e.g. copied from Seyda tables) into normalised rows.
Django-free on purpose so it can be tested without a database."""
import csv
import io
import re

from apps.core.persian import canonical, normalize_text, strip_links

MAX_ROWS = 5000

FIELDS = {
    "class_students": {
        "student_code": ["کد دانش آموز", "کد دانش‌آموز", "کد دانش آموزی", "کد دانش‌آموزی"],
        "first_name": ["نام"],
        "last_name": ["نام خانوادگی"],
        "grade": ["پایه"],
        "class_title": ["کلاس", "نام کلاس"],
    },
    "students": {
        "first_name": ["نام", "نام دانش آموز"],
        "last_name": ["نام خانوادگی"],
        "father_name": ["نام پدر"],
        "student_code": ["کد دانش آموز", "کد دانش‌آموزی", "کد"],
        "national_id": ["کد ملی"],
        "grade": ["پایه"],
        "class_title": ["کلاس", "عنوان کلاس"],
        "gender": ["جنسیت"],
        "field_code": ["کد رشته"],
        "field_name": ["عنوان رشته", "نام رشته"],
    },
    "classes": {
        "title": ["عنوان کلاس", "نام کلاس"],
        "code": ["کد کلاس"],
        "grade": ["پایه"],
        "field_code": ["کد رشته"],
        "field": ["نام رشته", "عنوان رشته", "رشته"],
        "capacity": ["ظرفیت", "ظرفیت کلاس"],
    },
    "student_update": {"national_id": ["کد ملی"], "student_code": ["کد دانش آموز", "کد دانش‌آموزی"],
                       "first_name": ["نام", "نام دانش آموز"], "last_name": ["نام خانوادگی"], "father_name": ["نام پدر"],
                       "mobile": ["موبایل", "شماره موبایل", "همراه"], "gender": ["جنسیت"]},
    "counties": {"name": ["نام شهرستان", "شهرستان", "نام"], "code": ["کد شهرستان", "کد"]},
    "regions": {"name": ["نام ناحیه", "نام منطقه", "ناحیه", "منطقه", "نام"], "code": ["کد ناحیه", "کد منطقه", "کد"]},
    "catalog_fields": {"code": ["کد رشته", "کد"], "name": ["نام رشته", "رشته", "نام"], "level_code": ["کد مقطع", "مقطع"]},
    "subjects": {
        "level": ["مقطع"],
        "field_code": ["کد رشته"],
        "code": ["کد درس"],
        "title": ["نام درس", "عنوان درس"],
        "grade": ["پایه"],
        "subject_type": ["نوع درس"],
        "field_name": ["نام رشته"],
        "start_date": ["تاریخ شروع"],
        "end_date": ["تاریخ پایان"],
    },
    "teachers": {
        "first_name": ["نام"],
        "last_name": ["نام خانوادگی"],
        "national_id": ["کد ملی"],
        "mobile": ["موبایل", "شماره موبایل"],
    },
    "curriculum": {
        "level_text": ["مقطع"],
        "field_code": ["کد رشته"],
        "field_name": ["نام رشته"],
        "subject_code": ["کد درس"],
        "subject_name": ["نام درس"],
        "grade": ["پایه", "پايه"],
        "selection": ["نوع درس"],
        "valid_from": ["تاریخ شروع"],
        "valid_to": ["تاریخ پایان"],
    },
    "fields": {
        "field_code": ["کد رشته"],
        "field_name": ["نام رشته", "عنوان"],
        "branch": ["شاخه"],
        "valid_from": ["شروع اعتبار"],
        "valid_to": ["پایان اعتبار"],
    },
    "staff": {
        "personnel_code": ["کد پرسنلی"],
        "first_name": ["نام"],
        "last_name": ["نام خانوادگی"],
        "district": ["نام منطقه"],
        "national_id": ["کد ملی"],
        "post": ["پست"],
        "school_name": ["مدرسه"],
        "school_code": ["کد مدرسه"],
        "period": ["دوره تحصیلی"],
        "year": ["سال تحصیلی"],
        "status": ["وضعیت"],
    },
    "class_teachers": {
        "course_name": ["نام درس"],
        "personnel_code": ["کد پرسنلی"],
        "first_name": ["نام معلم"],
        "last_name": ["نام خانوادگی معلم", "نام خانوادگی"],
        "father_name": ["نام پدر"],
        "national_id": ["کد ملی"],
        "group_code": ["کد گروه دوره", "کد گروه"],
        "class_title": ["کلاس"],
        "grade": ["پایه"],
    },
    "courses": {
        "title": ["نام درس", "عنوان درس"],
        "class_title": ["کلاس", "عنوان کلاس"],
        "teacher_national_id": ["کد ملی معلم", "کد ملی مدرس"],
    },
}
KIND_LABELS_EXTRA = {"counties": "شهرستان‌ها", "regions": "نواحی و مناطق", "catalog_fields": "رشته‌ها"}
KIND_LABELS_CLASS = {"class_teachers": "معلم و درس به تفکیک کلاس (سیدا: چاپ لیست کلاس)"}
KIND_LABELS = {"students": "دانش‌آموزان", "classes": "کلاس‌ها", "teachers": "دبیران", "courses": "درس‌های کلاس",
               "subjects": "سرفصل درس‌ها (کاتالوگ)"}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return normalize_text(v)


def _decode(data):
    for enc in ("utf-8-sig", "windows-1256"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("کدگذاری فایل شناخته نشد؛ فایل را با UTF-8 یا Windows-1256 ذخیره کنید.")


def _table_from_csv(text):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _table_from_xlsx(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()


def _split_line(line, known):
    """Pick the separator that actually splits the line into the most known header names / cells."""
    candidates = []
    if "\t" in line:
        candidates.append(line.split("\t"))
    if "|" in line:
        candidates.append(line.split("|"))
    if ";" in line:
        candidates.append(line.split(";"))
    if "،" in line or ("," in line and line.count(",") >= 1):
        candidates.append(re.split(r"[،,]", line))
    candidates.append(re.split(r"\s{2,}", line.strip()))
    if not candidates or len(candidates) == 1 and "\t" not in line:
        candidates.append([line])
    best = max(candidates, key=lambda c: (sum(1 for x in c if canonical(x) in known), len(c)))
    return [c.strip() for c in best]


def _table_from_paste(text, kind=None):
    """Browser / Seyda copies, in every layout seen so far:
      * column titles one per line, or several titles on one tab-separated line -> one header row (first one only)
      * a row number alone on a line (followed by blank lines) and the rest of the row on the next line -> joined
      * data rows: tab / ; | ، separated, at least three cells
      * page furniture (pagination, menus, counters, titles) is dropped."""
    known = {canonical(a) for fields in FIELDS.values() for aliases in fields.values() for a in aliases} | {
        canonical(x) for x in ("ردیف", "سال", "دوره", "نوع")}
    lines = []
    for raw in strip_links(text).splitlines():
        if raw.strip():
            lines.append(raw.strip())
    # join "row number" lines with the next non-empty line that carries the rest of the row
    merged, i = [], 0
    while i < len(lines):
        cur = lines[i]
        if re.fullmatch(r"\d{1,6}(\t\s*)*", cur) and i + 1 < len(lines) and lines[i + 1].count("\t") >= 2:
            merged.append(cur.rstrip("\t").strip() + "\t" + lines[i + 1])
            i += 2
            continue
        merged.append(cur)
        i += 1
    header, rows, header_done = [], [], False
    for line in merged:
        cells = [c.strip() for c in line.split("\t")] if "\t" in line else _split_line(line, known)
        cells = [c for c in cells if c != ""] or []
        if not cells:
            continue
        keys = [canonical(normalize_text(c)) for c in cells]
        is_header_line = all(k in known for k in keys) and not any(c.isdigit() for c in cells)
        if is_header_line:
            if not header_done:
                header.extend(normalize_text(c) for c in cells)
            continue
        if header and not header_done:
            rows.insert(0, header)
            header_done = True
        if len(cells) >= 3 and (cells[0].isdigit() or "\t" in line or any(c in line for c in ";|،,")):
            rows.append(cells)
    if header and not header_done:
        rows.insert(0, header)
    return rows


def _find_header(kind, table):
    """Spreadsheet/Seyda exports often have title rows above the header: take the first row that names >= 2 known columns."""
    lookup = {canonical(a) for aliases in FIELDS[kind].values() for a in aliases}
    for i, row in enumerate(table[:20]):
        if sum(1 for c in row if canonical(_cell(c)) in lookup) >= 2:
            return i
    return 0


def build_rows(kind, table):
    """Map header names (Persian aliases, spacing/ZWNJ/Arabic letters ignored) to field names."""
    if not table:
        raise ValueError("فایل خالی است.")
    start = _find_header(kind, table)
    table = table[start:]
    if len(table) - 1 > MAX_ROWS:
        raise ValueError(f"حداکثر {MAX_ROWS} ردیف در هر بار ورود مجاز است.")
    lookup = {canonical(a): f for f, aliases in FIELDS[kind].items() for a in aliases}
    mapping, unknown = {}, []
    for idx, raw in enumerate(table[0]):
        label = _cell(raw)
        if not label:
            continue
        field = lookup.get(canonical(label))
        if field and field not in mapping.values():
            mapping[idx] = field
        else:
            unknown.append(label)
    if not mapping:
        raise ValueError("سرستون‌ها شناسایی نشد. نام ستون‌ها باید مطابق راهنمای همین بخش باشد.")
    rows = []
    for n, raw in enumerate(table[1:], start=1):
        values = {f: (_cell(raw[i]) if i < len(raw) else "") for i, f in mapping.items()}
        if any(values.values()):
            rows.append({"n": n, "values": values})
    return rows, unknown



# ------------------------------------------------------------------ student list copied from «مشاهده مشخصات دانش‌آموزان»
_GRADE_WORDS = {"اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم", "نهم", "دهم", "یازدهم", "دوازدهم"}


def parse_students_copy(text):
    """Reads the copied rows of the Seyda student page. The copy loses the tabs, so the columns are found by their
    shape: index, student code, name words, birth date (13xxxxxx / 14xxxxxx), grade, field code, field name,
    type, gender, nationality, mobile, flaws. The name is split as: first word, father (last word), family (the middle)."""
    from apps.core.persian import normalize_text
    rows, flagged = [], []
    for raw in text.splitlines():
        t = normalize_text(raw).replace("\t", " ").split()
        if len(t) < 10 or not t[0].isdigit() or not t[1].isdigit():
            continue
        b = next((i for i, x in enumerate(t) if re.fullmatch(r"1[34]\d{6}", x)), None)
        if b is None or b < 3:
            continue
        g = next((i for i in range(b + 1, len(t)) if t[i] in _GRADE_WORDS), None)
        if g is None or g + 1 >= len(t) or not t[g + 1].isdigit():
            continue
        fc = g + 1
        gi = next((i for i in range(fc + 1, len(t)) if t[i] in ("دختر", "پسر")), None)
        if gi is None or gi - 1 <= fc:
            continue
        names = t[2:b]
        first = names[0]
        father = names[-1] if len(names) >= 2 else ""
        family = " ".join(names[1:-1]) if len(names) >= 3 else (names[1] if len(names) == 2 else "")
        mobile = next((x for x in t[gi + 1:] if re.fullmatch(r"9\d{9}", x)), "")
        values = {"student_code": t[1], "first_name": first, "last_name": family, "father_name": father,
                  "birth_date": t[b], "grade": t[g], "field_code": t[fc], "field": " ".join(t[fc + 1:gi - 1]),
                  "gender": t[gi], "mobile": mobile}
        if not family or not father:
            flagged.append(t[1])
        rows.append({"n": len(rows) + 1, "values": values})
    return rows, flagged


# ------------------------------------------------------------------ class and field lists copied with spaces (no tabs)
_BRANCH_WORDS = ("عمومی", "نظری", "کاردانش", "فنی و حرفه‌ای", "فنی حرفه‌ای و کاردانش", "حرفه‌ای استثنایی")


def parse_classes_copy(text):
    """«مشاهده مشخصات کلاس‌ها»: index, grade, field code, field name (words), class code (7 digits), class title, capacity."""
    from apps.core.persian import normalize_text
    rows = []
    for raw in text.splitlines():
        t = normalize_text(raw).replace("\t", " ").split()
        if len(t) < 6 or not t[0].isdigit() or t[1] not in _GRADE_WORDS or not t[2].isdigit():
            continue
        k = next((i for i in range(3, len(t) - 2) if re.fullmatch(r"\d{6,8}", t[i]) and t[i + 1].isdigit()), None)
        if k is None:
            continue
        rows.append({"n": len(rows) + 1, "values": {"grade": t[1], "field_code": t[2], "field": " ".join(t[3:k]),
                                                    "code": t[k], "title": t[k + 1], "capacity": t[k + 2]}})
    return rows, []


def _fields_from_class_list(text):
    """The class list («مشاهده مشخصات کلاس‌ها») also names every field: code and name, once per code."""
    seen, rows = set(), []
    for r in parse_classes_copy(text)[0]:
        v = r["values"]
        if v["field_code"] in seen or not v["field"]:
            continue
        seen.add(v["field_code"])
        rows.append({"n": len(rows) + 1, "values": {"field_code": v["field_code"], "field_name": v["field"], "branch": "",
                                                    "valid_from": "", "valid_to": ""}})
    return rows


def parse_fields_copy(text):
    """«نمایش رشته»: index, field code, field name (words), branch (a known word), valid-from, valid-to.
    When the text is the class list instead (no branch column), the fields are taken from it, once per code."""
    from apps.core.persian import normalize_text
    rows = _fields_from_class_list(text)
    if rows:
        return rows, []
    rows = []
    for raw in text.splitlines():
        t = normalize_text(raw).replace("\t", " ").split()
        if len(t) < 5 or not t[0].isdigit() or not re.fullmatch(r"\d{4,6}", t[1]):
            continue
        b = next((i for i in range(2, len(t)) if t[i] in _BRANCH_WORDS), None)
        if b is None or b <= 2:
            continue
        rows.append({"n": len(rows) + 1, "values": {"field_code": t[1], "field_name": " ".join(t[2:b]), "branch": t[b],
                                                    "valid_from": t[b + 1] if b + 1 < len(t) else "",
                                                    "valid_to": t[b + 2] if b + 2 < len(t) else ""}})
    return rows, []


GRADE_WORDS = {"هفتم", "هشتم", "نهم", "دهم", "یازدهم", "دوازدهم", "اول", "دوم", "سوم", "چهارم", "پنجم", "ششم"}


def parse_class_students_copy(text):
    """«تخصیص دانش‌آموز» از سیدا: ردیف، کد دانش‌آموزی، نام، نام خانوادگی (ممکن است چند کلمه باشد)، پایه.
    نام کلاس از خط «نام کلاس» خوانده می‌شود (مثلاً 161) و به همه‌ی ردیف‌ها داده می‌شود."""
    from apps.core.persian import normalize_text
    text = normalize_text(text)
    m = re.search(r"نام کلاس\s*[:：]?\s*(\S+)", text)
    class_title = m.group(1) if m else ""
    rows = []
    for raw in text.splitlines():
        # the row index may sit on its own line (SIDA copies it so); a row is a line that holds a student code
        t = raw.replace("\t", " ").split()
        code_at = next((i for i in range(0, min(3, len(t))) if re.fullmatch(r"\d{6,12}", t[i])), None)
        if code_at is None:
            continue
        rest = t[code_at + 1:]
        grade = rest[-1] if rest and rest[-1] in GRADE_WORDS else ""
        names = rest[:-1] if grade else rest
        if len(names) < 2:
            continue
        rows.append({"n": len(rows) + 1, "values": {
            "student_code": t[code_at], "first_name": names[0], "last_name": " ".join(names[1:]),
            "grade": grade, "class_title": class_title}})
    if not rows:
        rows = _class_students_tokens(text, class_title)
    return rows, []


def _class_students_tokens(text, class_title):
    """حالت دوم: مرورگر هر خانه‌ی جدول را در یک خط می‌گذارد (ردیف، کد، نام، نام خانوادگی، پایه، …).
    از روی توکن‌ها هر دانش‌آموز را از کد تا کد بعدی می‌خوانیم؛ کلمه‌های سرستون و عدد ردیف کنار گذاشته می‌شوند."""
    import re as _re
    skip = {"select", "ردیف", "کد", "نام", "خانوادگی", "پایه", "کلاس", "عملیات", "دانش‌آموزی", "دانش‌آموز", "جستجو", "فیلتر"}
    tokens = [x for x in _re.split(r"\s+", text) if x]
    rows = []
    idx = [i for i, x in enumerate(tokens) if _re.fullmatch(r"\d{6,12}", x)]
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(tokens)
        rest = [x for x in tokens[i + 1:end] if x not in skip and not x.isdigit()]
        grade = rest[-1] if rest and rest[-1] in GRADE_WORDS else ""
        names = rest[:-1] if grade else rest
        # the row index that came before the code must not be in the names
        if len(names) < 2:
            continue
        rows.append({"n": len(rows) + 1, "values": {
            "student_code": tokens[i], "first_name": names[0], "last_name": " ".join(names[1:]),
            "grade": grade, "class_title": class_title}})
    return rows


def parse_class_teachers_copy(text):
    """«تخصیص معلم به درس گروه» copied with spaces: index, grade, class (title-code), group code, lesson (code- name),
    personnel code, first name, family name (the rest of the line)."""
    from apps.core.persian import normalize_text
    rows = []
    for raw in text.splitlines():
        t = normalize_text(raw).replace("\t", " ").split()
        if len(t) < 7 or not t[0].isdigit() or "-" not in t[2]:
            continue
        p = next((i for i in range(4, len(t) - 2) if re.fullmatch(r"\d{5,10}", t[i])), None)
        if p is None:
            continue
        title, _, code = t[2].partition("-")
        rows.append({"n": len(rows) + 1, "values": {
            "grade": t[1], "class_title": title, "group_code": t[3] if t[3].isdigit() else code, "_class_code": code,
            "course_name": " ".join(t[4:p]), "personnel_code": t[p], "first_name": t[p + 1],
            "last_name": " ".join(t[p + 2:])}})
    return rows, []


def parse(kind, *, name="", data=None, text=None):
    if kind not in FIELDS:
        raise ValueError("نوع ورود نامعتبر است.")
    if text is not None and kind == "class_teachers":
        # the table reader may read the rows without their cells; then the copied layout is read by its shape
        try:
            table_rows, unknown = build_rows(kind, _table_from_paste(text, kind))
        except ValueError:
            table_rows, unknown = [], []
        usable = [r for r in table_rows if (r["values"].get("first_name") or r["values"].get("personnel_code"))]
        if usable:
            return table_rows, unknown
        shape_rows, _ = parse_class_teachers_copy(text)
        return (shape_rows, []) if shape_rows else (table_rows, unknown)
    if text is not None and kind == "class_students":
        rows, _ = parse_class_students_copy(text)
        return rows, []
    if text is not None and kind in ("classes", "fields"):
        # the copied layout with spaces is read by shape; a copy with tabs by the header. Keep the reader that finds more rows.
        try:
            table_rows, unknown = build_rows(kind, _table_from_paste(text, kind))
        except ValueError:
            table_rows, unknown = [], []
        shape_rows, _ = (parse_classes_copy if kind == "classes" else parse_fields_copy)(text)
        return (shape_rows, []) if len(shape_rows) > len(table_rows) else (table_rows, unknown)
    if text is not None and kind == "students":
        # a copy with tabs is read by the table reader; a copy with spaces only by the shape reader. Keep the one that reads more rows.
        try:
            table_rows, unknown = build_rows(kind, _table_from_paste(text, kind))
        except ValueError:
            table_rows, unknown = [], []
        shape_rows, _ = parse_students_copy(text)
        # the copied student layout is read by its shape whenever it finds rows: the table reader can take the
        # header lines of the multi-line copy for data
        if shape_rows:
            return shape_rows, []
        return table_rows, unknown
    if text is not None:
        table = _table_from_paste(text, kind)
        if kind == "class_teachers":
            heading = re.search(r"کلاس\s*:\s*([0-9۰-۹]+)", strip_links(text))
            if heading:
                class_code = normalize_text(heading.group(1))
                rows, unknown = build_rows(kind, table)
                for r in rows:
                    r["values"]["class_title"] = r["values"].get("class_title") or class_code
                return rows, unknown
    else:
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if ext == "xlsx":
            table = _table_from_xlsx(data)
        elif ext in ("csv", "txt"):
            table = _table_from_csv(_decode(data))
        else:
            raise ValueError("فقط فایل CSV یا XLSX پذیرفته می‌شود (فایل قدیمی XLS را ذخیره‌شده به XLSX تبدیل کنید).")
    return build_rows(kind, table)
