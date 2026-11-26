from datetime import date

from apps.accounts.models import SchoolMembership, User
from apps.core.tests_phase2 import Base, PW
from apps.cultural.models import Competition, CompetitionResult, Trip


class CulturalTests(Base):
    def setUp(self):
        super().setUp()
        self.culture = User.objects.create_user("culture1", password=PW, role="school_deputy_culture", org=self.school)
        SchoolMembership.objects.create(user=self.culture, school=self.school)
        self.s1.org = self.school
        self.s1.save()

    def test_competition_top_five_with_manual_scores(self):
        r = self.api(self.culture).post("/api/cultural/competitions/", {"school": self.school.pk, "title": "قرآن", "date": "2026-12-01", "kind": "festival"}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        cid = r.data["id"]
        ok = self.api(self.culture).put(f"/api/cultural/competitions/{cid}/results/", {"results": [
            {"rank": 1, "student": self.s1.pk, "score": "19.5"}, {"rank": 2, "name": "گروه الف", "score": "18"}]}, format="json")
        self.assertEqual(ok.status_code, 200, getattr(ok, "data", ""))
        self.assertEqual(CompetitionResult.objects.filter(competition_id=cid).count(), 2)
        bad = self.api(self.culture).put(f"/api/cultural/competitions/{cid}/results/", {"results": [{"rank": 6, "name": "x"}]}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_students_see_only_published_results(self):
        c = Competition.objects.create(school=self.school, title="ورزشی", date=date(2026, 12, 2), is_published=False)
        self.assertEqual(self.api(self.s1).get(f"/api/cultural/competitions/?school={self.school.pk}").data, [])
        c.is_published = True
        c.save()
        self.assertEqual(len(self.api(self.s1).get(f"/api/cultural/competitions/?school={self.school.pk}").data), 1)

    def test_trip_participants_and_consent(self):
        r = self.api(self.culture).post("/api/cultural/trips/", {"school": self.school.pk, "title": "اردو مشهد", "destination": "طوس",
                                                                 "start_date": "2026-12-10", "end_date": "2026-12-11"}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        tid = r.data["id"]
        p = self.api(self.culture).put(f"/api/cultural/trips/{tid}/participants/", {"participants": [
            {"student": self.s1.pk, "consent": "accepted", "guardian_name": "علی"}]}, format="json")
        self.assertEqual(p.status_code, 200, getattr(p, "data", ""))
        consent = self.api(self.s1).get(f"/api/cultural/trips/{tid}/consent/{self.s1.pk}/")
        self.assertEqual(consent.status_code, 200)
        self.assertEqual(consent.data["consent"], "رضایت داده")
        self.assertEqual(self.api(self.t1).post("/api/cultural/trips/", {"school": self.school.pk, "title": "x", "start_date": "2026-12-10", "end_date": "2026-12-11"}, format="json").status_code, 403)

    def test_trip_end_before_start_refused(self):
        r = self.api(self.culture).post("/api/cultural/trips/", {"school": self.school.pk, "title": "x", "start_date": "2026-12-11", "end_date": "2026-12-10"}, format="json")
        self.assertEqual(r.status_code, 400)


class FestivalConsentParentTests(Base):
    def setUp(self):
        super().setUp()
        from apps.accounts.models import ParentLink
        self.culture = User.objects.create_user("culture_p", password=PW, role="school_deputy_culture", org=self.school)
        SchoolMembership.objects.create(user=self.culture, school=self.school)
        self.s1.org = self.school
        self.s1.save()
        self.parent = User.objects.create_user("parent_fc", password=PW, role="parent")
        ParentLink.objects.create(parent=self.parent, student=self.s1, is_verified=True)

    def test_festival_consent_and_parent_overview(self):
        c = Competition.objects.create(school=self.school, title="جشنواره قرآن", date=date(2026, 12, 1), kind="festival", is_published=True)
        r = self.api(self.culture).put(f"/api/cultural/competitions/{c.pk}/participants/", {"participants": [
            {"student": self.s1.pk, "consent": "accepted", "guardian_name": "پدر"}]}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        form = self.api(self.parent).get(f"/api/cultural/competitions/{c.pk}/consent/{self.s1.pk}/")
        self.assertEqual(form.status_code, 200)
        self.assertEqual(form.data["consent"], "رضایت داده")
        ov = self.api(self.parent).get(f"/api/children/{self.s1.pk}/overview/")
        self.assertEqual(ov.status_code, 200)
        self.assertIn("discipline", ov.data)
        self.assertEqual(ov.data["festivals"][0]["consent"], "رضایت داده")
