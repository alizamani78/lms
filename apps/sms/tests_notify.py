from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.accounts.models import ParentLink, User
from apps.attendance.models import AttendanceRecord
from apps.core.tests_phase2 import Base, PW
from apps.sms.models import SmsGateway, SmsOutbox
from apps.sms.crypto import encrypt_secret
from apps.sms.notify import flush, scan_deadlines


class ParentNotificationTests(Base):
    def setUp(self):
        super().setUp()
        self.parent = User.objects.create_user("par1", password=PW, role="parent", mobile="09121112233")
        ParentLink.objects.create(parent=self.parent, student=self.s1, is_verified=True)
        SmsGateway.objects.create(school=self.school, name="پنل", base_url="https://sms.example.ir/x",
                                  auth_secret_enc=encrypt_secret("K"), is_active=True)

    def test_absence_queues_one_message_per_parent_and_event(self):
        rec = AttendanceRecord.objects.create(student=self.s1, classroom=self.classroom, date=timezone.localdate(),
                                              slot_key="c0p9", status="absent")
        self.assertEqual(SmsOutbox.objects.filter(parent=self.parent, purpose="attendance_absent").count(), 1)
        rec.status = "absent"; rec.save()                     # saving again does not send it twice
        self.assertEqual(SmsOutbox.objects.count(), 1)

    def test_unverified_or_opted_out_parent_gets_nothing(self):
        ParentLink.objects.filter(parent=self.parent).update(notify_sms=False)
        AttendanceRecord.objects.create(student=self.s1, classroom=self.classroom, date=timezone.localdate(), slot_key="c0p8", status="late")
        self.assertEqual(SmsOutbox.objects.count(), 0)

    def test_flush_sends_through_the_school_panel(self):
        AttendanceRecord.objects.create(student=self.s1, classroom=self.classroom, date=timezone.localdate(), slot_key="c0p7", status="absent")
        with mock.patch("apps.sms.notify.send", return_value=(True, "HTTP 200")):
            self.assertEqual(flush(), 1)
        self.assertEqual(SmsOutbox.objects.get().status, "sent")

    def test_missing_homework_and_exam_absence_after_deadline(self):
        from apps.coursework.models import Assignment
        from apps.exams.models import Exam
        now = timezone.now()
        Assignment.objects.create(course=self.course, title="تمرین", due_at=now - timedelta(hours=1), is_published=True)
        Exam.objects.create(course=self.course, title="آزمون", starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1), is_published=True)
        scan_deadlines()
        purposes = set(SmsOutbox.objects.values_list("purpose", flat=True))
        self.assertIn("homework_missing", purposes)
        self.assertIn("exam_absent", purposes)
        scan_deadlines()                                       # the scan is repeatable: nothing is duplicated
        self.assertEqual(SmsOutbox.objects.filter(purpose="exam_absent").count(), 1)
