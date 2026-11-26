from django.test import SimpleTestCase


class JalaliTests(SimpleTestCase):
    def test_known_nowruz_dates(self):
        from apps.core.jalali import to_jalali
        self.assertEqual(to_jalali(2026, 3, 21), (1405, 1, 1))
        self.assertEqual(to_jalali(2025, 3, 21), (1404, 1, 1))
        self.assertEqual(to_jalali(2024, 3, 20), (1403, 1, 1))

    def test_a_date_in_autumn_and_persian_digits(self):
        import datetime
        from apps.core.jalali import format_jalali, to_jalali
        self.assertEqual(to_jalali(2026, 11, 14), (1405, 8, 23))
        self.assertEqual(format_jalali(datetime.date(2026, 11, 14), with_time=False), "۲۳ آبان ۱۴۰۵")
