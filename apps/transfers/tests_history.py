from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import OrgUnit, User
from apps.academics.models import ClassRoom
from apps.core.tests_phase2 import Base, PW
from apps.transfers.models import StudentSchoolHistory, open_history


class SchoolHistoryTests(Base):
    def test_transfer_keeps_both_periods(self):
        open_history(self.s1, self.school, reason="ورود اولیه", on_date=timezone.localdate() - timedelta(days=100))
        dest = ClassRoom.objects.create(school=self.school2, academic_year=self.year, name="دهم ب۳", grade=self.grade)
        from apps.transfers.models import TransferRequest
        t = TransferRequest.objects.create(student=self.s1, from_school=self.school, to_school=self.school2,
                                           to_classroom=dest, reason="انتقال", status="ready")
        from apps.transfers.services import execute
        execute(t, self.s1, dest)
        open_history(self.s1, self.school2, reason="انتقال", transfer=t)
        periods = list(StudentSchoolHistory.objects.filter(student=self.s1).order_by("start_date"))
        self.assertEqual([p.school_id for p in periods], [self.school.pk, self.school2.pk])
        self.assertIsNotNone(periods[0].end_date)                         # old school period closed
        self.assertIsNone(periods[1].end_date)                            # new school period open
        self.assertLess(periods[0].end_date, periods[1].start_date)

    def test_new_school_admin_sees_the_whole_trail(self):
        open_history(self.s1, self.school, on_date=timezone.localdate() - timedelta(days=50))
        open_history(self.s1, self.school2, on_date=timezone.localdate() - timedelta(days=10))
        self.s1.org = self.school2
        self.s1.save()
        r = self.api(self.sa2 if hasattr(self, "sa2") else self.sa).get(f"/api/students/{self.s1.pk}/history/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["periods"]), 2)
        self.assertEqual(self.api(self.t2).get(f"/api/students/{self.s1.pk}/history/").status_code, 404)
