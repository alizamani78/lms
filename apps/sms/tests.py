from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.tests_phase2 import Base, PW
from apps.sms.crypto import decrypt_secret, encrypt_secret
from apps.sms.models import PasswordResetCode, SmsGateway, SmsLog
from apps.sms.providers import render_body
from apps.sms.recovery import normalize_mobile


class SmsBase(Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.s1.mobile = "09121234567"
        self.s1.save()
        self.gw = SmsGateway.objects.create(school=self.school, name="پنل", base_url="https://sms.example.ir/send",
                                            auth_secret_enc=encrypt_secret("KEY-123"), sender="10001234",
                                            is_active=True)

    def code_sent(self):
        with mock.patch("apps.sms.recovery.send", return_value=(True, "HTTP 200")) as m:
            self.anon().post("/api/recovery/request/", {"username": "s1", "mobile": "09121234567"}, format="json")
        if not m.called:
            return None
        msg = m.call_args[0][2]
        return msg.split(": ")[1].split("\n")[0]

    def anon(self):
        return APIClient()


class RecoveryTests(SmsBase):
    def test_request_is_generic_and_sends_only_to_file_mobile(self):
        with mock.patch("apps.sms.recovery.send", return_value=(True, "HTTP 200")) as m:
            r1 = self.anon().post("/api/recovery/request/", {"username": "s1", "mobile": "09121234567"}, format="json")
            r2 = self.anon().post("/api/recovery/request/", {"username": "nobody", "mobile": "09121234567"}, format="json")
            r3 = self.anon().post("/api/recovery/request/", {"username": "s1", "mobile": "09000000000"}, format="json")
        self.assertEqual((r1.status_code, r2.status_code, r3.status_code), (202, 202, 202))
        self.assertEqual(r1.data, r2.data)
        self.assertEqual(m.call_count, 1)                       # only the correct mobile triggers an SMS
        self.assertEqual(SmsLog.objects.count(), 1)
        self.assertNotIn("1234567", SmsLog.objects.get().to_masked)

    def test_school_without_panel_sends_nothing(self):
        self.gw.is_active = False
        self.gw.save()
        with mock.patch("apps.sms.recovery.send") as m:
            self.anon().post("/api/recovery/request/", {"username": "s1", "mobile": "09121234567"}, format="json")
        m.assert_not_called()

    def test_verify_changes_password_once_and_revokes(self):
        code = self.code_sent()
        self.assertIsNotNone(code)
        bad = self.anon().post("/api/recovery/verify/", {"username": "s1", "code": "000000", "new_password": "N3w-Strong-pass"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.anon().post("/api/recovery/verify/", {"username": "s1", "code": code, "new_password": "N3w-Strong-pass"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.anon().post("/api/recovery/verify/", {"username": "s1", "code": code, "new_password": "An0ther-pass"}, format="json").status_code, 400)
        login = self.anon().post("/api/auth/login/", {"username": "s1", "password": "N3w-Strong-pass"}, format="json")
        self.assertEqual(login.status_code, 200)

    def test_code_stored_hashed_and_limited_tries(self):
        code = self.code_sent()
        rec = PasswordResetCode.objects.get(user=self.s1)
        self.assertNotEqual(rec.code_hash, code)
        for _ in range(5):
            self.anon().post("/api/recovery/verify/", {"username": "s1", "code": "111111", "new_password": "N3w-Strong-pass"}, format="json")
        self.assertEqual(self.anon().post("/api/recovery/verify/", {"username": "s1", "code": code, "new_password": "N3w-Strong-pass"}, format="json").status_code, 400)

    def test_mobile_normalisation(self):
        self.assertEqual(normalize_mobile("۰۹۱۲ ۱۲۳ ۴۵۶۷"), "09121234567")
        self.assertEqual(normalize_mobile("+989121234567"), "09121234567")
        self.assertIsNone(normalize_mobile("0812"))


class GatewayTests(SmsBase):
    def test_secret_never_returned_and_scope(self):
        r = self.api(self.sa).get(f"/api/schools/{self.school.pk}/sms/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data[0]["has_secret"])
        self.assertNotIn("KEY-123", str(r.data))
        self.assertEqual(self.api(self.t1).get(f"/api/schools/{self.school.pk}/sms/").status_code, 403)

    def test_https_only_and_private_targets_refused(self):
        r = self.api(self.sa).post(f"/api/schools/{self.school.pk}/sms/",
                                   {"name": "x", "base_url": "http://sms.example.ir/x"}, format="json")
        self.assertEqual(r.status_code, 400)
        g = SmsGateway.objects.create(school=self.school, name="local", base_url="https://127.0.0.1/send")
        with mock.patch("apps.sms.views.send", side_effect=__import__("apps.sms.providers", fromlist=["SmsError"]).SmsError("x")):
            r = self.api(self.sa).post(f"/api/schools/{self.school.pk}/sms/{g.pk}/test/", {"mobile": "09121234567"}, format="json")
        self.assertFalse(r.data["accepted"])

    def test_template_escaping(self):
        body = render_body('{"to": "{to}", "message": "{message}"}', "09121234567", "1", 'say "hi"\n')
        self.assertIn(b'say \\"hi\\"', body)
        with self.assertRaises(Exception):
            render_body('{"to": "{to}"', "x", "y", "z")

    def test_secret_crypto_roundtrip(self):
        self.assertEqual(decrypt_secret(encrypt_secret("abc")), "abc")
