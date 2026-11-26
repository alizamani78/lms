from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.integrations.models import ExternalSystem
from apps.liveclass.models import ClassSession
from apps.liveclass.providers import PROVIDERS

from .tests_phase2 import Base


class ClassroomEndpointTests(Base):
    def test_classrooms_and_roster_are_scoped(self):
        d = self.api(self.t1).get("/api/classrooms/").data
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["daily"], [c["id"] for c in d[0]["courses"]]), (True, [self.course.pk]))
        self.assertEqual(self.api(self.t2).get("/api/classrooms/").data, [])
        self.assertEqual(len(self.api(self.sa).get("/api/classrooms/").data), 1)
        self.assertEqual(self.api(self.s1).get("/api/classrooms/").status_code, 403)
        self.assertEqual(len(self.api(self.t1).get(f"/api/classrooms/{self.classroom.pk}/students/").data), 3)
        self.assertEqual(self.api(self.t2).get(f"/api/classrooms/{self.classroom.pk}/students/").status_code, 404)
        self.assertEqual(self.api(self.parent).get(f"/api/classrooms/{self.classroom.pk}/students/").status_code, 403)


class ContentManagementTests(Base):
    def url(self):
        return f"/api/courses/{self.course.pk}/content/"

    def test_create_publish_delete(self):
        t, st = self.api(self.t1), self.api(self.s1)
        r = t.post(self.url(), {"title": "جزوه", "kind": "text", "body": "متن درس"}, format="json")
        self.assertEqual(r.status_code, 201)
        cid = r.data["id"]
        self.assertEqual(st.get(self.url()).data, [])                       # draft is hidden from students
        self.assertEqual(len(t.get(self.url()).data), 1)
        self.assertEqual(t.patch(f"/api/content/{cid}/", {"is_published": True}, format="json").status_code, 200)
        self.assertEqual(len(st.get(self.url()).data), 1)
        self.assertEqual(self.api(self.t2).patch(f"/api/content/{cid}/", {"is_published": False}, format="json").status_code, 404)
        self.assertEqual(st.patch(f"/api/content/{cid}/", {"is_published": False}, format="json").status_code, 403)
        self.assertEqual(t.delete(f"/api/content/{cid}/").status_code, 204)
        self.assertEqual(t.get(self.url()).data, [])

    def test_validation_and_permissions(self):
        t = self.api(self.t1)
        bad = {"title": "x", "kind": "link", "url": "javascript:alert(1)"}
        self.assertEqual(t.post(self.url(), bad, format="json").status_code, 400)
        self.assertEqual(t.post(self.url(), {"title": "x", "kind": "text"}, format="json").status_code, 400)
        ok = {"title": "جلسه اول", "kind": "text", "body": "<p>سلام <b>دانش‌آموزان</b></p>"}
        self.assertEqual(t.post(self.url(), ok, format="json").status_code, 201)
        self.assertEqual(self.api(self.s1).post(self.url(), ok, format="json").status_code, 403)
        self.assertEqual(self.api(self.t2).post(self.url(), ok, format="json").status_code, 404)


@mock.patch.dict("os.environ", {"HOOK_SECRET": "s3cret"})
class SessionCreationTests(Base):
    def body(self, **extra):
        now = timezone.now()
        d = {"title": "جلسه", "provider": "bbb", "starts_at": (now + timedelta(hours=1)).isoformat(),
             "ends_at": (now + timedelta(hours=2)).isoformat()}
        d.update(extra)
        return d

    def test_requires_a_configured_system(self):
        r = self.api(self.t1).post(f"/api/courses/{self.course.pk}/sessions/", self.body(), format="json")
        self.assertEqual(r.status_code, 400)

    def test_create_list_and_scope(self):
        ExternalSystem.objects.create(name="bbb1", kind="bbb", base_url="https://bbb.example.ir/bigbluebutton",
                                      secret_env="HOOK_SECRET")
        url = f"/api/courses/{self.course.pk}/sessions/"
        with mock.patch.object(PROVIDERS["bbb"], "create_meeting", return_value=None):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.api(self.t1).post(url, self.body(), format="json")
        self.assertEqual(r.status_code, 201)
        s = ClassSession.objects.get(pk=r.data["id"])
        self.assertEqual((s.remote_status, s.auto_create), ("ready", True))
        bad = self.body(ends_at=(timezone.now() - timedelta(hours=1)).isoformat())
        self.assertEqual(self.api(self.t1).post(url, bad, format="json").status_code, 400)
        self.assertEqual(self.api(self.t1).post(url, self.body(auto_create=False), format="json").status_code, 400)  # needs id
        self.assertEqual(self.api(self.t2).post(url, self.body(), format="json").status_code, 404)
        self.assertEqual(self.api(self.s1).post(url, self.body(), format="json").status_code, 403)
        staff_row = self.api(self.t1).get("/api/live/").data[0]
        student_row = self.api(self.s1).get("/api/live/").data[0]
        self.assertIn("attendance_status", staff_row)
        self.assertNotIn("attendance_status", student_row)
        self.assertEqual(self.api(self.s4).get("/api/live/").data, [])
        self.assertEqual(self.api(self.parent).get("/api/live/").data, [])
