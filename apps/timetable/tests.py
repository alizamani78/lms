from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.academics.models import ClassRoom
from apps.accounts.models import User
from apps.core.jalali import (gregorian_to_jalali, is_jalali_leap, jalali_month_days, jalali_str, jalali_to_gregorian,
                              weekday_index)
from apps.core.tests_phase2 import Base, PW
from apps.courses.models import Course, Enrollment

from .models import BellPeriod, ScheduleEntry


class JalaliTests(SimpleTestCase):
    def test_known_dates(self):
        self.assertEqual(gregorian_to_jalali(2026, 10, 26), (1405, 8, 4))
        self.assertEqual(jalali_to_gregorian(1405, 1, 1), (2026, 3, 21))          # Nowruz 1405
        self.assertEqual(jalali_to_gregorian(1404, 1, 1), (2025, 3, 21))
        self.assertEqual(jalali_to_gregorian(1403, 12, 30), (2025, 3, 20))        # 1403 is a leap year
        self.assertEqual(jalali_to_gregorian(1405, 7, 1), (2026, 9, 23))          # 1 Mehr 1405: school year starts
        self.assertEqual([y for y in range(1398, 1412) if is_jalali_leap(y)], [1399, 1403, 1408])
        self.assertEqual((jalali_month_days(1404, 12), jalali_month_days(1403, 12)), (29, 30))

    def test_round_trip_for_decades(self):
        d = date(2000, 1, 1)
        while d < date(2060, 1, 1):
            jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
            self.assertEqual(jalali_to_gregorian(jy, jm, jd), (d.year, d.month, d.day))
            self.assertTrue(1 <= jd <= jalali_month_days(jy, jm))
            d += timedelta(days=1)

    def test_weekday_and_format(self):
        self.assertEqual(weekday_index(date(2026, 10, 24)), 0)      # Saturday
        self.assertEqual(weekday_index(date(2026, 10, 30)), 6)      # Friday
        self.assertEqual(jalali_str(date(2026, 10, 26)), "1405/08/04")


class TimetableTests(Base):
    PERIODS = [{"number": 1, "start": "08:00", "end": "08:45"}, {"number": 2, "start": "08:50", "end": "09:35"}]

    def setUp(self):
        super().setUp()
        self.cls2 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="دهم ب",
                                             grade=self.grade, homeroom_teacher=self.t1)
        self.course2 = Course.objects.create(org=self.school, title="فیزیک", teacher=self.t1, classroom=self.cls2)
        self.sa2 = User.objects.create_user("sa2", password=PW, role="school_admin", org=self.school2)
        self.sa_api = self.api(self.sa)
        self.bell_url = f"/api/schedule/bell/{self.school.pk}/"
        self.grid1 = f"/api/schedule/grid/{self.classroom.pk}/"
        self.grid2 = f"/api/schedule/grid/{self.cls2.pk}/"
        self.assertEqual(self.sa_api.put(self.bell_url, {"periods": self.PERIODS}, format="json").status_code, 200)

    def entry(self, course, weekday=0, period=1, room=""):
        return {"weekday": weekday, "period": period, "course": course.pk, "room": room}

    def test_bell_validation_and_scope(self):
        bad = [{"number": 1, "start": "08:00", "end": "09:00"}, {"number": 2, "start": "08:30", "end": "09:30"}]
        self.assertEqual(self.sa_api.put(self.bell_url, {"periods": bad}, format="json").status_code, 400)       # overlap
        dup = [{"number": 1, "start": "08:00", "end": "08:45"}, {"number": 1, "start": "09:00", "end": "09:45"}]
        self.assertEqual(self.sa_api.put(self.bell_url, {"periods": dup}, format="json").status_code, 400)      # duplicate number
        self.assertEqual(self.api(self.t1).put(self.bell_url, {"periods": self.PERIODS}, format="json").status_code, 403)
        self.assertEqual(self.api(self.sa2).put(self.bell_url, {"periods": self.PERIODS}, format="json").status_code, 404)
        self.assertEqual(self.api(self.ra).put(self.bell_url, {"periods": self.PERIODS}, format="json").status_code, 200)
        self.assertEqual(len(self.sa_api.get(self.bell_url).data), 2)

    def test_manage_and_view(self):
        r = self.sa_api.put(self.grid1, {"entries": [self.entry(self.course, 0, 1, "۱۰۱")]}, format="json")
        self.assertEqual(r.status_code, 200)
        s = self.api(self.s1).get("/api/schedule/").data["entries"]
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["weekday_name"], s[0]["start"], s[0]["course"], s[0]["room"], s[0]["teacher"] != ""),
                         ("شنبه", "08:00", "ریاضی", "۱۰۱", True))
        self.assertEqual(len(self.api(self.t1).get("/api/schedule/").data["entries"]), 1)
        self.assertEqual(self.api(self.t2).get("/api/schedule/").data["entries"], [])        # other school's teacher
        self.assertEqual(self.api(self.s4).get("/api/schedule/").data["entries"], [])        # not in this class
        self.assertEqual(self.api(self.parent).get("/api/schedule/").data["entries"], [])
        self.assertEqual(len(self.sa_api.get(f"/api/schedule/?classroom={self.classroom.pk}").data["entries"]), 1)

    def test_students_and_teachers_cannot_edit(self):
        body = {"entries": [self.entry(self.course)]}
        self.assertEqual(self.api(self.s1).put(self.grid1, body, format="json").status_code, 403)
        self.assertEqual(self.api(self.t1).put(self.grid1, body, format="json").status_code, 403)
        self.assertEqual(self.api(self.sa2).put(self.grid1, body, format="json").status_code, 404)   # another school's admin
        self.assertEqual(self.api(self.t1).get(self.grid1).status_code, 403)

    def test_validation_errors(self):
        post = lambda entries: self.sa_api.put(self.grid1, {"entries": entries}, format="json")
        self.assertEqual(post([self.entry(self.course, 0, 7)]).status_code, 400)                      # undefined period
        self.assertEqual(post([self.entry(self.course2)]).status_code, 400)                           # course of another class
        self.assertEqual(post([self.entry(self.course), self.entry(self.course)]).status_code, 400)   # same cell twice

    def test_teacher_cannot_be_in_two_classes_at_once(self):
        self.assertEqual(self.sa_api.put(self.grid1, {"entries": [self.entry(self.course, 0, 1)]}, format="json").status_code, 200)
        r = self.sa_api.put(self.grid2, {"entries": [self.entry(self.course2, 0, 1)]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("برنامه دارد", r.data["detail"])
        self.assertEqual(self.sa_api.put(self.grid2, {"entries": [self.entry(self.course2, 0, 2)]}, format="json").status_code, 200)
        # replacing a class's own grid never conflicts with itself
        self.assertEqual(self.sa_api.put(self.grid1, {"entries": [self.entry(self.course, 0, 1)]}, format="json").status_code, 200)

    def test_used_period_cannot_be_deleted(self):
        self.sa_api.put(self.grid1, {"entries": [self.entry(self.course, 0, 1)]}, format="json")
        only2 = {"periods": [self.PERIODS[1]]}
        self.assertEqual(self.sa_api.put(self.bell_url, only2, format="json").status_code, 409)
        self.assertEqual(BellPeriod.objects.filter(school=self.school).count(), 2)
        self.sa_api.put(self.grid1, {"entries": []}, format="json")
        self.assertEqual(self.sa_api.put(self.bell_url, only2, format="json").status_code, 200)

    def test_model_clean_matches_api_rules(self):
        from django.core.exceptions import ValidationError
        b1 = BellPeriod.objects.get(school=self.school, number=1)
        ScheduleEntry.objects.create(classroom=self.classroom, weekday=0, period=b1, course=self.course)
        clash = ScheduleEntry(classroom=self.cls2, weekday=0, period=b1, course=self.course2)
        with self.assertRaises(ValidationError):
            clash.full_clean()
