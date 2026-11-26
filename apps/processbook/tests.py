from apps.accounts.models import SchoolMembership, User
from apps.core.tests_phase2 import Base, PW
from apps.processbook.models import ProcessDefinition, ProcessEntry, TeacherEvaluation


class ProcessBookTests(Base):
    def setUp(self):
        super().setUp()
        self.dep = User.objects.create_user("dep_pb", password=PW, role="school_deputy_edu", org=self.school)
        SchoolMembership.objects.create(user=self.dep, school=self.school)
        self.s1.org = self.school
        self.s1.save()

    def _define(self, sensitive=False):
        r = self.api(self.dep).post("/api/processbook/definitions/", {"school": self.school.pk, "title": "وضعیت جسمی", "sensitive": sensitive,
            "fields": [{"label": "بیماری", "type": "text", "required": True}, {"label": "نوع خون", "type": "choice", "choices": "A، B، O"}]}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        return r.data["id"]

    def test_only_the_educational_deputy_defines_processes(self):
        self.assertEqual(self.api(self.t1).post("/api/processbook/definitions/", {"school": self.school.pk, "title": "x", "fields": []}, format="json").status_code, 403)

    def test_entry_is_validated_against_the_questions(self):
        did = self._define()
        missing = self.api(self.t1).post("/api/processbook/entries/", {"definition": did, "student": self.s1.pk, "values": {}}, format="json")
        self.assertEqual(missing.status_code, 400)
        bad_choice = self.api(self.t1).post("/api/processbook/entries/", {"definition": did, "student": self.s1.pk,
                                                                          "values": {"f1": "آسم", "f2": "Z"}}, format="json")
        self.assertEqual(bad_choice.status_code, 400)
        ok = self.api(self.t1).post("/api/processbook/entries/", {"definition": did, "student": self.s1.pk,
                                                                  "values": {"f1": "آسم", "f2": "A"}}, format="json")
        self.assertEqual(ok.status_code, 201, getattr(ok, "data", ""))

    def test_sensitive_process_hidden_from_teacher_book(self):
        did = self._define(sensitive=True)
        self.api(self.t1).post("/api/processbook/entries/", {"definition": did, "student": self.s1.pk, "values": {"f1": "آسم"}}, format="json")
        teacher_view = self.api(self.t1).get(f"/api/processbook/student/{self.s1.pk}/").data
        self.assertEqual(teacher_view["processes"], [])
        deputy_view = self.api(self.dep).get(f"/api/processbook/student/{self.s1.pk}/").data
        self.assertEqual(len(deputy_view["processes"]), 1)

    def test_teacher_evaluation_average(self):
        r = self.api(self.dep).post("/api/processbook/evaluations/", {"school": self.school.pk, "teacher": self.t1.pk, "scores": {
            "teaching": 5, "class_management": 4, "interaction": 4, "punctuality": 3, "development": 4}}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        self.assertEqual(r.data["average"], 4.0)
        self.assertEqual(self.api(self.t1).post("/api/processbook/evaluations/", {"school": self.school.pk, "teacher": self.t1.pk, "scores": {}}, format="json").status_code, 403)
