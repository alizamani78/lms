from datetime import date
from io import StringIO

from django.core.management import call_command

from apps.core.tests_phase2 import Base, PW
from apps.accounts.models import User
from apps.schoolcal.models import Occasion
from apps.schoolcal.occasions_data import OCCASIONS


class OccasionTests(Base):
    def test_seed_loads_the_list_and_is_repeatable(self):
        call_command("seed_occasions", stdout=StringIO())
        n = Occasion.objects.count()
        self.assertEqual(n, len(OCCASIONS))
        call_command("seed_occasions", stdout=StringIO())
        self.assertEqual(Occasion.objects.count(), n)                       # running it again adds nothing
        self.assertEqual(Occasion.objects.filter(jalali_year=1405, jalali_month=1, jalali_day=1).first().gregorian_date, date(2026, 3, 21))
        self.assertEqual(Occasion.objects.filter(jalali_year=1405, category="holiday", is_holiday=True).count(), 26)
        self.assertEqual(Occasion.objects.filter(jalali_year=1406, category="holiday", is_holiday=True).count(), 29)

    def test_api_returns_the_range_only(self):
        call_command("seed_occasions", stdout=StringIO())
        r = self.api(self.s1).get("/api/occasions/?from=2026-03-21&to=2026-03-24")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all("2026-03-21" <= str(x["date"]) <= "2026-03-24" for x in r.data))
        self.assertGreaterEqual(len(r.data), 3)
