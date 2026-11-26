from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import SchoolMembership, User
from apps.core.tests_phase2 import Base, PW
from apps.sms.models import SmsGateway, SmsOutbox


class MeetingTests(Base):
    def setUp(self):
        super().setUp()
        self.dep = User.objects.create_user("dep_meet", password=PW, role="school_deputy_edu", org=self.school, mobile="09121110001")
        SchoolMembership.objects.create(user=self.dep, school=self.school)
        self.t1.mobile = "09121110002"
        self.t1.save()
        self.start = timezone.now() + timedelta(days=2)
        SmsGateway.objects.create(school=self.school, name="پنل", base_url="https://sms.example.ir/x", auth_secret_enc="x", is_active=True)

    def _meeting(self, user=None):
        r = self.api(user or self.sa).post("/api/meetings/", {"school": self.school.pk, "kind": "teachers_council", "title": "شورای معلمین",
                                                              "starts_at": self.start.isoformat(), "ends_at": (self.start + timedelta(hours=2)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        return r.data["id"]

    def test_end_must_be_after_start(self):
        r = self.api(self.sa).post("/api/meetings/", {"school": self.school.pk, "title": "x", "starts_at": self.start.isoformat(),
                                                      "ends_at": (self.start - timedelta(hours=1)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_invitations_queue_sms_once_and_print_data(self):
        mid = self._meeting()
        r = self.api(self.sa).put(f"/api/meetings/{mid}/invitations/", {"invitees": [self.t1.pk, self.dep.pk]}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        self.assertEqual(r.data["sms_queued"], 2)
        again = self.api(self.sa).put(f"/api/meetings/{mid}/invitations/", {"invitees": [self.t1.pk, self.dep.pk]}, format="json")
        self.assertEqual(again.data["sms_queued"], 0)
        self.assertEqual(SmsOutbox.objects.filter(purpose="meeting_invite").count(), 2)
        p = self.api(self.t1).get(f"/api/meetings/{mid}/invitation/{self.t1.pk}/")
        self.assertEqual(p.status_code, 200)
        self.assertEqual(p.data["title"], "شورای معلمین")

    def test_minutes_are_cleaned_and_decisions_have_follow_up(self):
        mid = self._meeting()
        r = self.api(self.sa).patch(f"/api/meetings/{mid}/", {"minutes": '<p onclick="x()">مصوب شد</p><script>bad()</script>'}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("onclick", r.data["minutes"])
        self.assertNotIn("<script", r.data["minutes"])
        d = self.api(self.sa).post(f"/api/meetings/{mid}/decisions/", {"text": "برنامه امتحانات", "follow_up": self.dep.pk, "due_date": "2026-12-20"}, format="json")
        self.assertEqual(d.status_code, 201, getattr(d, "data", ""))
        self.assertEqual(d.data["decisions"][0]["follow_up_name"], self.dep.get_full_name() or self.dep.username)

    def test_only_managers_manage_and_invitee_reads_own(self):
        mid = self._meeting()
        self.assertEqual(self.api(self.t1).post("/api/meetings/", {"school": self.school.pk, "title": "x", "starts_at": self.start.isoformat(),
                                                                   "ends_at": (self.start + timedelta(hours=1)).isoformat()}, format="json").status_code, 403)
        self.api(self.sa).put(f"/api/meetings/{mid}/invitations/", {"invitees": [self.t1.pk]}, format="json")
        self.assertEqual(self.api(self.t1).get(f"/api/meetings/{mid}/").status_code, 200)
        self.assertEqual(self.api(self.s1).get(f"/api/meetings/{mid}/").status_code, 403)
