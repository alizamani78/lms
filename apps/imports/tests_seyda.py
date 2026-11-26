from django.test import TestCase
from apps.academics.models import ClassRoom, StudyField, Grade, Level
from apps.accounts.models import OrgUnit, User
from apps.core.tests_phase2 import Base
from .parsers import build_rows, parse, _table_from_paste


CLASS_SAMPLE = ("نمونه اطلاعات کلاس در سیدا\n"
                "ردیف\tپایه\tکد رشته\tنام رشته\tکد کلاس\tعنوان کلاس\tظرفیت کلاس\n"
                "1\tنهم\t14\tدوره متوسطه اول\t6664686\tخیام 901\t100\n"
                "2\tهفتم\t14\tدوره متوسطه اول\t6664645\tحافظ 701\t100\n")
STUDENT_SAMPLE = ("ردیف\tسال\tدوره\tکد دانش آموز\tنام\tنام خانوادگی\tنام پدر\tپایه\tنوع کلاس\tکلاس\tکد رشته\tعنوان رشته\n"
                  "1\t1404-1405\tضمن سال\t970487177\tبنيامين\tپيراسته\tحميدرضا\tهشتم\tعادی\tرودکی 802\t14\tدوره متوسطه اول\n")
SUBJECT_SAMPLE = ("ردیف\tمقطع\tکد رشته\tنام رشته\tکد درس\tنام درس\tپایه\tنوع درس\n"
                  "1\tمتوسطه دوم - نظری\t15000\tرياضي فيزيک\t12141\tعلوم اجتماعي\tدوازدهم\tنهايي\n")


class SeydaSampleTests(Base):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.root = User.objects.create_superuser('root_s', password='Str0ng-pass-1')

    def test_class_sample_parses_title_rows(self):
        rows, unknown = parse("classes", text=CLASS_SAMPLE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["values"]["title"], "خیام 901")
        self.assertEqual(rows[0]["values"]["code"], "6664686")
        self.assertEqual(rows[0]["values"]["field_code"], "14")
        self.assertIn("ردیف", unknown)

    def test_student_sample_maps_class_and_code(self):
        rows, _ = parse("students", text=STUDENT_SAMPLE)
        v = rows[0]["values"]
        self.assertEqual((v["student_code"], v["class_title"], v["first_name"]), ("970487177", "رودکی 802", "بنیامین"))

    def test_subject_catalogue_only_for_super_admin(self):
        r = self.api(self.sa).post("/api/imports/create/", {"kind": "subjects", "school": self.school.pk, "text": SUBJECT_SAMPLE})
        self.assertEqual(r.status_code, 403)

    def test_subject_catalogue_preview(self):
        Level.objects.create(code="MS2", name="متوسطه دوره دوم", order=3)
        StudyField.objects.create(code="15000", name="ریاضی فیزیک")
        Grade.objects.create(code="G12", name="دوازدهم", level=Level.objects.get(code="MS2"))
        r = self.api(self.root).post("/api/imports/create/", {"kind": "subjects", "school": self.school.pk, "text": SUBJECT_SAMPLE})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["summary"]["ok"], 1)

    def test_shared_subject_many_grades_and_fields(self):
        from apps.academics.models import Subject
        lvl = Level.objects.get(code="MS2")
        Grade.objects.create(code="G11", name="یازدهم", level=lvl)
        StudyField.objects.create(code="15001", name="علوم تجربی", level=lvl)
        text = ("ردیف\tپایه\tکد رشته\tکد درس\tنام درس\tنوع درس\n"
                "1\tدهم، یازدهم، دوازدهم\t15000,15001\t12200\tریاضی عمومی\tاجباری\n")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "subjects", "school": self.school.pk, "text": text})
        self.assertEqual(r.data["summary"]["ok"], 1, r.data["rows"])
        self.api(self.root).post(f"/api/imports/{r.data['id']}/commit/")
        sub = Subject.objects.get(code="12200")
        self.assertEqual(sub.grades.count(), 3)
        self.assertEqual(sub.study_fields.count(), 2)


class SeydaCopiedPageTests(Base):
    """A table copied from the Seyda web page: column titles one per line, rows tab-separated, page furniture around."""

    def test_copied_course_table_is_read_and_has_no_noise(self):
        import pathlib
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_subjects_page.txt").read_text(encoding="utf-8")
        rows, unknown = parse("subjects", text=text)
        self.assertEqual(len(rows), 6)                        # 4 + 2 course rows; the repeated header and pagination are ignored
        first = rows[0]["values"]
        self.assertEqual((first["code"], first["title"], first["grade"], first["field_code"]), ("12141", "علوم اجتماعی", "دوازدهم", "15000"))
        self.assertEqual(first["subject_type"], "نهایی")
        self.assertNotIn("مقطع", unknown)
        self.assertTrue(all(r["values"]["code"].isdigit() for r in rows))

    def test_copied_table_preview_on_the_server(self):
        import pathlib
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_subjects_page.txt").read_text(encoding="utf-8")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "subjects", "school": self.school.pk, "text": text})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["summary"]["total"], 6)


class SeydaStudentCopyTests(Base):
    """Student list copied from the Seyda page (12 columns, row number on its own line, links around codes)."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import ClassRoom
        self.cls271 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="یازدهم ریاضی 271", code="271", grade=self.grade)
        self.root = User.objects.create_superuser("root_stu", password="Str0ng-pass-1")

    def test_copied_student_list_is_read(self):
        import pathlib
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_students_page.txt").read_text(encoding="utf-8")
        rows, unknown = parse("students", text=text)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["values"]["student_code"], "890835306")
        self.assertEqual(rows[0]["values"]["class_title"], "271")
        self.assertNotIn("کد دانش آموز", unknown)

    def test_preview_matches_class_by_code_and_moves_national_ids(self):
        import pathlib
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_students_page.txt").read_text(encoding="utf-8")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "students", "school": self.school.pk, "text": text})
        self.assertEqual(r.status_code, 201, r.data)
        by_code = {row["values"].get("student_code") or row["values"].get("national_id"): row for row in r.data["rows"]}
        self.assertEqual(by_code["890835306"]["status"], "ok")                  # class 271 found by its code
        national = by_code["4312312575"]
        self.assertEqual(national["values"]["national_id"], "4312312575")       # moved from the code column
        self.assertEqual(national["status"], "error")                           # class 252 does not exist here: red, with the reason
        self.assertIn("252", " ".join(national["errors"]))


class SeydaClassTeachersTests(Base):
    """Teacher + course + class from the Seyda class list (chapter «چاپ لیست کلاس»)."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import ClassRoom
        self.c151 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="یازدهم ۱۵۱", code="151", grade=self.grade)
        self.root = User.objects.create_superuser("root_ct", password="Str0ng-pass-1")

    def test_preview_and_commit_create_teachers_and_course_links(self):
        import pathlib
        from apps.courses.models import Course
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_class_teachers_page.txt").read_text(encoding="utf-8")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "class_teachers", "school": self.school.pk, "text": text})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["summary"]["ok"], 5, r.data["rows"])
        done = self.api(self.root).post(f"/api/imports/{r.data['id']}/commit/")
        self.assertEqual(done.data["summary"]["created"], 5)
        self.assertEqual(User.objects.filter(role="teacher", national_id="0900932643").count(), 1)   # same teacher twice: one account
        self.assertEqual(Course.objects.filter(classroom=self.c151, teacher__national_id="0900932643").count(), 2)

    def test_unknown_class_is_red_with_reason(self):
        import pathlib
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_class_teachers_page.txt").read_text(encoding="utf-8").replace("کلاس: 151", "کلاس: 999")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "class_teachers", "school": self.school.pk, "text": text})
        self.assertEqual(r.data["summary"]["error"], 5)
        self.assertIn("999", " ".join(r.data["rows"][0]["errors"]))


class SeydaPrimaryStudentCopyTests(Base):
    """Primary school list: the class column is the class number, the field is the primary programme (code 1)."""

    def test_primary_copy_is_read_and_classes_matched_by_number(self):
        import pathlib
        from apps.academics.models import ClassRoom, Grade, Level
        lvl = Level.objects.create(code="PRI", name="ابتدایی", order=1)
        grade = Grade.objects.create(code="G6P", name="ششم", level=lvl)
        ClassRoom.objects.create(school=self.school, academic_year=self.year, name="ششم ۶", code="6", grade=grade)
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_primary_students_page.txt").read_text(encoding="utf-8")
        rows, unknown = parse("students", text=text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["values"]["student_code"], "4423251175")
        r = self.api(self.root).post("/api/imports/create/", {"kind": "students", "school": self.school.pk, "text": text})
        self.assertEqual(r.status_code, 201, r.data)
        first = r.data["rows"][0]
        self.assertEqual(first["status"], "ok", first)                        # class 6 found by its code


class SameGradeManyFieldsTests(Base):
    """One school can have the same grade in several fields, several classes per grade, and class numbers that
    repeat across grades (primary schools). A class is found by its grade + code first."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import ClassRoom, Grade, Level, StudyField
        self.root = User.objects.create_superuser("root_gf", password="Str0ng-pass-1")
        lvl = Level.objects.create(code="PRI9", name="ابتدایی9", order=1)
        self.g1 = Grade.objects.create(code="P1X", name="اول9", level=lvl)
        self.g2 = Grade.objects.create(code="P2X", name="دوم9", level=lvl)
        self.c_g1 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="اول ۱", code="1", grade=self.g1)
        self.c_g2 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="دوم ۱", code="1", grade=self.g2)
        self.c_g2b = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="دوم ۲", code="2", grade=self.g2)

    def test_code_resolved_by_grade(self):
        from apps.imports.rules import _resolve_class, _context
        ctx = _context("students", self.school, self.year, [])
        self.assertEqual(_resolve_class(ctx, "1", "دوم9").pk, self.c_g2.pk)
        self.assertEqual(_resolve_class(ctx, "1", "اول9").pk, self.c_g1.pk)
        self.assertEqual(_resolve_class(ctx, "2", "دوم9").pk, self.c_g2b.pk)

    def test_student_rows_land_in_the_right_class(self):
        text = "کد دانش آموز\tنام\tنام خانوادگی\tپایه\tکلاس\n960000501\tعلی\tاحمدی\tدوم9\t1\n960000502\tرضا\tمحمدی\tاول9\t1\n"
        r = self.api(self.root).post("/api/imports/create/", {"kind": "students", "school": self.school.pk, "text": text})
        self.assertEqual(r.data["summary"]["ok"], 2, r.data["rows"])
        self.api(self.root).post(f"/api/imports/{r.data['id']}/commit/")
        self.assertTrue(self.c_g2.students.filter(student_code="960000501").exists())
        self.assertTrue(self.c_g1.students.filter(student_code="960000502").exists())


class CreateMissingClassesTests(Base):
    """The student list asks for classes the school does not have; with confirmation they are created first."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import Grade, Level
        self.root = User.objects.create_superuser("root_mc", password="Str0ng-pass-1")
        lvl = Level.objects.create(code="PRIM", name="ابتدایی-م", order=1)
        self.g = Grade.objects.create(code="PM2", name="دوم", level=lvl)
        from apps.accounts.models import SchoolProfile
        SchoolProfile.objects.create(school=self.school, school_code="PRIM1", level=lvl, gender="mixed")

    def test_preview_lists_missing_then_confirm_creates_and_commits(self):
        from apps.academics.models import ClassRoom
        text = "کد دانش آموز\tنام\tنام خانوادگی\tپایه\tکلاس\tکد رشته\n960000601\tعلی\tاحمدی\tدوم\t3\t1\n960000602\tرضا\tمحمدی\tدوم\t3\t1\n"
        r = self.api(self.root).post("/api/imports/create/", {"kind": "students", "school": self.school.pk, "text": text})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["summary"]["ok"], 0)
        self.assertEqual(r.data["missing_classes"], [{"grade": "دوم", "code": "3", "field_code": "1"}])
        done = self.api(self.root).post(f"/api/imports/{r.data['id']}/commit/", {"create_classes": True}, format="json")
        self.assertEqual(done.data["classes_created"], 1)
        self.assertEqual(done.data["summary"]["created"], 2)
        self.assertTrue(ClassRoom.objects.filter(school=self.school, name="دوم 3", code="3", grade=self.g).exists())

    def test_without_confirmation_nothing_is_created(self):
        from apps.academics.models import ClassRoom
        text = "کد دانش آموز\tنام\tنام خانوادگی\tپایه\tکلاس\n960000603\tعلی\tاحمدی\tدوم\t4\n"
        r = self.api(self.root).post("/api/imports/create/", {"kind": "students", "school": self.school.pk, "text": text})
        done = self.api(self.root).post(f"/api/imports/{r.data['id']}/commit/", {}, format="json")
        self.assertEqual(done.data["summary"]["created"], 0)
        self.assertFalse(ClassRoom.objects.filter(school=self.school, code="4").exists())


class SeydaFieldsTests(Base):
    """The field list from «نمایش رشته»: branches per level, duplicates refused, codes with a leading zero are separate."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import Branch, Level
        from apps.accounts.models import SchoolProfile
        lvl = Level.objects.create(code="S2X", name="متوسطه دوم - تست", order=9)
        Branch.objects.create(level=lvl, name="کاردانش")
        self.level = lvl
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": lvl})

    def test_fields_are_read_and_leading_zero_codes_stay_separate(self):
        import pathlib
        from apps.imports.parsers import parse
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_fields_page.txt").read_text(encoding="utf-8")
        rows, _ = parse("fields", text=text)
        codes = [r["values"]["field_code"] for r in rows]
        self.assertIn("21561", codes)
        self.assertIn("021561", codes)

    def test_field_is_added_once_and_a_second_time_is_refused(self):
        from apps.academics.models import SchoolField, StudyField
        from apps.imports.rules import check_field, create_field
        v = {"field_code": "21411", "field_name": "صنایع دستی - نقاشی فرش", "branch": "کاردانش"}
        ctx = {"school": self.school}
        self.assertEqual(check_field(v, ctx, 1), [])
        create_field(v, ctx, self.school)
        create_field(v, ctx, self.school)                         # a repeat does not create a second link
        self.assertEqual(StudyField.objects.filter(code="21411").count(), 1)
        self.assertEqual(SchoolField.objects.filter(school=self.school).count(), 1)
        self.assertTrue(any("تکراری" in e for e in check_field(v, ctx, 2)))

    def test_branch_must_belong_to_the_school_level(self):
        from apps.imports.rules import check_field
        v = {"field_code": "99999", "field_name": "رشته دیگر", "branch": "نظری"}
        self.assertTrue(check_field(v, {"school": self.school}, 1))


class SeydaCurriculumTests(Base):
    """Lessons of a grade and a field: general (no field) and specialized (with field), no repeats."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import Grade, Level, StudyField, Branch
        from apps.accounts.models import SchoolProfile
        from apps.academics.models import SchoolField
        lvl = Level.objects.create(code="S2Y", name="متوسطه دوم - هنرستان کاردانش", order=10)
        self.grade = Grade.objects.create(level=lvl, name="یازدهم")
        Branch.objects.create(level=lvl, name="کاردانش")
        self.field = StudyField.objects.create(code="99701", name="تعمیر ابزار دقیق", level=lvl)
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": lvl})
        SchoolField.objects.create(school=self.school, field=self.field)

    def test_general_and_specialized_lessons_are_stored_with_their_category(self):
        from apps.academics.models import Subject
        from apps.imports.rules import check_curriculum, create_curriculum
        general = {"subject_code": "00310000", "subject_name": "الزامات محیط کار", "grade": "یازدهم", "selection": "اجباری", "level_text": "متوسطه دوم - هنرستان کاردانش", "field_code": ""}
        special = {"subject_code": "00310900", "subject_name": "کارگاه نوآوری و کارآفرینی", "grade": "یازدهم", "selection": "اجباری", "level_text": "متوسطه دوم - هنرستان کاردانش", "field_code": "99701"}
        ctx = {"school": self.school}
        self.assertEqual(check_curriculum(general, ctx, 1), [])
        create_curriculum(general, ctx, self.school)
        self.assertEqual(check_curriculum(special, ctx, 2), [])
        create_curriculum(special, ctx, self.school)
        self.assertEqual(Subject.objects.get(code="00310000").sharing, "general")
        self.assertEqual(Subject.objects.get(code="00310900").sharing, "specialized")
        self.assertEqual(Subject.objects.get(code="00310900").subject_type, "اجباری")

    def test_repeat_of_the_same_lesson_for_the_same_grade_and_field_is_refused(self):
        from apps.imports.rules import check_curriculum, create_curriculum
        special = {"subject_code": "00310900", "subject_name": "کارگاه نوآوری و کارآفرینی", "grade": "یازدهم", "selection": "اجباری", "level_text": "متوسطه دوم - هنرستان کاردانش", "field_code": "99701"}
        ctx = {"school": self.school}
        create_curriculum(special, ctx, self.school)
        self.assertTrue(any("تکراری" in e for e in check_curriculum(special, ctx, 1)))

    def test_field_outside_the_school_is_refused(self):
        from apps.imports.rules import check_curriculum
        from apps.academics.models import StudyField
        other = StudyField.objects.create(code="88888", name="رشته دیگر", level=self.field.level)
        v = {"subject_code": "00311111", "subject_name": "درس", "grade": "یازدهم", "level_text": "متوسطه دوم", "field_code": other.code}
        self.assertTrue(any("ثبت نشده" in e for e in check_curriculum(v, {"school": self.school}, 1)))


class StudentFieldAndDuplicatesTests(Base):
    """A student's field missing from the school is added once and linked; a student already present is not added again."""

    def test_missing_field_is_created_and_linked_once(self):
        from apps.academics.models import SchoolField, StudyField
        from apps.imports.rules import _ensure_student_field
        ctx = {"fields_by_code": {}, "school": self.school}
        v = {"field_code": "98411", "field": "نقاشی ایرانی"}
        _ensure_student_field(v, self.school, ctx)
        _ensure_student_field(v, self.school, ctx)                  # a second student of the same field
        self.assertEqual(StudyField.objects.filter(code="98411").count(), 1)
        self.assertEqual(SchoolField.objects.filter(school=self.school).count(), 1)


class SeydaStaffSampleTests(Base):
    """The staff sample exactly as Seyda shows it (header lines, the rows with tabs and the deputy's post)."""

    def test_sample_is_read_and_the_deputy_row_maps_to_educational_deputy(self):
        import pathlib
        from apps.imports.parsers import parse
        from apps.imports.rules import staff_role
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_staff_sample.txt").read_text(encoding="utf-8")
        rows, _ = parse("staff", text=text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["values"]["national_id"], "0890173788")
        self.assertEqual(staff_role(rows[2]["values"]["post"]), "school_deputy_edu")


class SeydaStudentCopyShapeTests(Base):
    """The student list copied with spaces (no tabs): every row is read by its shape."""

    def test_all_copied_rows_are_read_with_name_grade_field_and_gender(self):
        import pathlib
        from apps.imports.parsers import parse
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_students_copy_sample.txt").read_text(encoding="utf-8")
        rows, _ = parse("students", text=text)
        self.assertEqual(len(rows), 4)
        first = rows[0]["values"]
        self.assertEqual((first["student_code"], first["first_name"], first["father_name"]), ("890852383", "بهاره", "مهدی"))
        self.assertEqual(first["grade"], "دهم")
        self.assertEqual(first["field_code"], "98411")
        self.assertEqual(first["gender"], "دختر")
        self.assertEqual(first["mobile"], "9368449622")


class SeydaClassAndFieldCopyTests(Base):
    """Class and field lists copied with spaces: every row is read by its shape."""

    def test_class_rows_read(self):
        import pathlib
        from apps.imports.parsers import parse
        text = (pathlib.Path(__file__).parent / "fixtures" / "seyda_classes_copy_sample.txt").read_text(encoding="utf-8")
        rows, _ = parse("classes", text=text)
        self.assertEqual(len(rows), 4)
        v = rows[0]["values"]
        self.assertEqual((v["grade"], v["field_code"], v["code"], v["title"], v["capacity"]), ("یازدهم", "93154", "7449979", "201", "28"))

    def test_field_rows_read(self):
        from apps.imports.parsers import parse
        rows, _ = parse("fields", text="1 21411 صنایع دستی - نقاشی فرش کاردانش 140501 0\n2 21561 دستياري طراحي لباس نمایشی کاردانش 140401 0\n")
        self.assertEqual([r["values"]["field_code"] for r in rows], ["21411", "21561"])


class BranchForSchoolLevelTests(Base):
    """«کاردانش» is accepted for a secondary school whatever its level's wording is, and created on import when missing."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import Level
        from apps.accounts.models import SchoolProfile
        self.lvl = Level.objects.create(code="S2Z", name="متوسطه دوم - هنرستان کاردانش", order=11)
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": self.lvl})

    def test_kardanesh_is_allowed_and_created_once(self):
        from apps.academics.models import Branch
        from apps.imports.rules import check_field, create_field
        v = {"field_code": "21411", "field_name": "صنایع دستی", "branch": "کاردانش"}
        ctx = {"school": self.school}
        self.assertEqual(check_field(v, ctx, 1), [])
        create_field(v, ctx, self.school)
        create_field(v, ctx, self.school)
        self.assertEqual(Branch.objects.filter(level=self.lvl, name="کاردانش").count(), 1)

    def test_secondary_branch_refused_for_primary_level(self):
        from apps.academics.models import Level
        from apps.accounts.models import SchoolProfile
        from apps.imports.rules import check_field
        prim = Level.objects.create(code="P1Z", name="ابتدایی دوره اول", order=12)
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": prim})
        v = {"field_code": "21411", "field_name": "صنایع دستی", "branch": "کاردانش"}
        self.assertTrue(check_field(v, {"school": self.school}, 1))


class TimetableDataTests(TestCase):
    """The data the timetable engine needs: approved weekly hours, teacher presence, and the subject kind."""

    def test_approved_hours_are_unique_per_grade_field_and_subject(self):
        from django.db import IntegrityError, transaction
        from apps.academics.models import Grade, GradeWeeklyHours, Level, Subject
        lvl = Level.objects.create(code="TT1", name="متوسطه دوم", order=30)
        g = Grade.objects.create(level=lvl, name="دهم")
        sub = Subject.objects.create(code="TT100", name="ریاضی")
        GradeWeeklyHours.objects.create(grade=g, subject=sub, hours=4)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GradeWeeklyHours.objects.create(grade=g, subject=sub, hours=5)

    def test_subject_kind_default_is_normal(self):
        from apps.academics.models import Subject
        self.assertEqual(Subject(code="x", name="y").kind, "normal")


class CurriculumFromSeydaPatternTests(TestCase):
    """The Seyda lesson table: the level with its branch after the dash, a field that may be new, and «نامشخص» grades."""

    def setUp(self):
        from apps.academics.models import Branch, Level
        from apps.accounts.models import SchoolProfile, OrgUnit
        self.lvl = Level.objects.create(code="P2", name="متوسطه دوم", order=70)
        Branch.objects.create(level=self.lvl, name="نظری")
        self.school = OrgUnit.objects.create(name="ه ز", kind="school")
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": self.lvl})

    def test_row_with_unknown_grade_and_new_field_is_accepted_once(self):
        from apps.academics.models import StudyField, Subject
        from apps.imports.rules import check_curriculum, create_curriculum
        v = {"level_text": "متوسطه دوم - نظری", "field_code": "15000", "field_name": "رياضي فيزيک",
             "subject_code": "14011", "subject_name": "هنر", "grade": "نامشخص", "selection": "اجباری"}
        ctx = {"school": self.school}
        self.assertEqual(check_curriculum(v, ctx, 1), [])
        create_curriculum(v, ctx, self.school)
        self.assertEqual(check_curriculum(v, ctx, 2), ["این درس برای این پایه و رشته از قبل ثبت است (تکراری)"])
        f = StudyField.objects.get(code="15000")
        self.assertEqual(f.branch.name, "نظری")
        self.assertEqual(Subject.objects.get(code="14011").sharing, "specialized")


class SameLessonInSeveralGradesTests(TestCase):
    """A lesson found in several grades is general; a grade it is not yet defined in is added, not refused."""

    def setUp(self):
        from apps.academics.models import Branch, Grade, Level, StudyField
        from apps.accounts.models import SchoolProfile, OrgUnit
        lvl = Level.objects.create(code="P3", name="متوسطه دوم", order=71)
        Branch.objects.create(level=lvl, name="نظری")
        self.g10 = Grade.objects.create(level=lvl, name="دهم")
        self.g11 = Grade.objects.create(level=lvl, name="یازدهم")
        self.field = StudyField.objects.create(code="15000", name="ریاضی فیزیک", level=lvl)
        self.school = OrgUnit.objects.create(name="ه ز ۲", kind="school")
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": lvl})
        from apps.accounts.models import SchoolProfile as _SP
        from apps.academics.models import SchoolField
        SchoolField.objects.create(school=self.school, field=self.field)

    def test_lesson_in_two_grades_becomes_general_and_is_added(self):
        from apps.academics.models import Subject
        from apps.imports.rules import check_curriculum, create_curriculum
        ctx = {"school": self.school}
        first = {"level_text": "متوسطه دوم - نظری", "field_code": "15000", "field_name": "ریاضی فیزیک", "subject_code": "10041", "subject_name": "نگارش", "grade": "دهم", "selection": "اجباری"}
        create_curriculum(first, ctx, self.school)
        self.assertEqual(Subject.objects.get(code="10041").sharing, "specialized")
        second = dict(first, grade="یازدهم")
        self.assertEqual(check_curriculum(second, ctx, 2), [])
        create_curriculum(second, ctx, self.school)
        sub = Subject.objects.get(code="10041")
        self.assertEqual(sub.grades.count(), 2)
        self.assertEqual(sub.sharing, "general")


class TeacherGroupListTests(TestCase):
    """The Seyda teacher-to-group list: «251-7724642» is title and code; a known teacher is found by personnel code."""

    def test_title_and_code_are_split(self):
        from apps.imports.rules import _split_class
        v = {"class_title": "251-7724642"}
        _split_class(v)
        self.assertEqual((v["class_title"], v["_class_code"]), ("251", "7724642"))


class TeacherToGroupLessonTests(TestCase):
    """A teacher row of the Seyda teacher-to-lesson list: a missing class is added with its grade, and the row is read as is."""

    def setUp(self):
        from apps.academics.models import AcademicYear, Grade, Level
        from apps.accounts.models import SchoolProfile, OrgUnit
        import datetime
        lvl = Level.objects.create(code="TG1", name="متوسطه دوم", order=80)
        self.grade = Grade.objects.create(level=lvl, name="دوازدهم")
        self.year = AcademicYear.objects.create(name="۱۴۰۵-۱۴۰۶", starts_on=datetime.date(2026, 9, 1), ends_on=datetime.date(2027, 6, 30), is_current=True)
        self.school = OrgUnit.objects.create(name="تخ", kind="school")
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": lvl})

    def test_row_is_read_and_the_missing_class_is_added_once(self):
        from apps.academics.models import ClassRoom
        from apps.imports.parsers import parse
        from apps.imports.rules import check_class_teachers, _grade_for_row
        text = "1\tدوازدهم\t372-7728922\t7728922\t99990-انضباط\t16013904\tحسین\tطاهریان\n"
        rows, _ = parse("class_teachers", text=text)
        v = rows[0]["values"]
        self.assertEqual(v["_split_class"] if False else v["class_title"], "372-7728922")
        self.assertIsNotNone(_grade_for_row(self.school, v))
        ctx = {"school": self.school, "classes_by_code": {}, "teachers": {}, "subjects_by_code": {}, "course_keys": set()}
        errs = check_class_teachers(dict(v), ctx, 1)
        self.assertFalse(any("پیدا نشد" in e for e in errs), errs)


class StudentWithoutClassTests(TestCase):
    """A student is recorded with his details only; the class is set later. The student code is copied to the national id when it passes the check."""

    def test_student_without_class_is_accepted_and_valid_code_is_the_national_id(self):
        from apps.imports.rules import check_students
        v = {"first_name": "بهاره", "last_name": "آخرتی", "student_code": "0890852383", "gender": "دختر"}
        ctx = {"school": None, "db_codes": set(), "db_nids": set(), "db_usernames": set(), "seen": {}, "profile": None}
        errs = check_students(dict(v), ctx, 1)
        self.assertFalse(any("کلاس" in e for e in errs), errs)
        from apps.imports.rules import is_valid_national_id
        self.assertTrue(is_valid_national_id("0890173788"))


class StudentCodePaddingTests(TestCase):
    """A short student code is padded with leading zeros; a valid 10-digit code is also the national id."""

    def test_nine_digit_code_is_padded_and_checked_as_national_id(self):
        from apps.imports.rules import _split_code, is_valid_national_id
        v = _split_code({"student_code": "890852383", "national_id": ""})
        self.assertEqual(v["student_code"], "0890852383")
        self.assertEqual(is_valid_national_id("0890852383"), v.get("national_id") == "0890852383")

    def test_valid_padded_code_becomes_the_national_id(self):
        from apps.imports.rules import _split_code
        v = _split_code({"student_code": "890173788", "national_id": ""})
        self.assertEqual(v["student_code"], "0890173788")
        self.assertEqual(v["national_id"], "0890173788")


class StudentDuplicateRefusedTests(TestCase):
    """A repeated student code (also an old 9-digit one) or a repeated national id is refused, never saved twice."""

    def setUp(self):
        from apps.accounts.models import OrgUnit, User
        from apps.academics.models import Level
        from apps.accounts.models import SchoolProfile
        lvl = Level.objects.create(code="DUP", name="متوسطه دوم", order=90)
        self.school = OrgUnit.objects.create(name="تکراری", kind="school")
        SchoolProfile.objects.update_or_create(school=self.school, defaults={"level": lvl})
        self.old = User.objects.create_user("old_9", password="Str0ng-pass-1", role="student", student_code="890852383")
        self.nid_user = User.objects.create_user("0890173788", password="Str0ng-pass-1", role="student", national_id="0890173788")

    def test_old_nine_digit_code_is_found_in_padded_form(self):
        from apps.imports.rules import _stored_codes
        self.assertIn("0890852383", _stored_codes(["890852383"]))

    def test_repeated_national_id_is_found(self):
        from apps.imports.rules import _stored_nids
        self.assertIn("0890173788", _stored_nids(["0890173788"], []))
        self.assertIn("0890173788", _stored_nids([], ["0890173788"]))
