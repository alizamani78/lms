"""آزمون موتور وزن زنگ (بدون دیتابیس): پر شدن ساعت‌ها، نبود تکرار در یک روز، فاقد درس، و ثابت ماندن زنگ‌های یک‌ساعته."""
import unittest

from .solver_weighted import solve_weighted


def _hours_of(res, cid, name):
    return sum(sz for (sl, lst) in res["shared"].get(cid, {}).items() for (n, sz) in lst if n == name)


class WeightedBellTests(unittest.TestCase):
    def test_three_hours_split_two_plus_one(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 3, "teacher": 10}]}]
        res = solve_weighted(classes, [0, 1], [1, 2], {1: 2, 2: 2})
        self.assertEqual(_hours_of(res, 1, "A"), 3)
        self.assertEqual(res["errors"], [])

    def test_leftover_hour_of_two_hour_bell_is_filled_by_same_class_one_hour_lesson(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 3, "teacher": 10}, {"name": "B", "hours": 1, "teacher": 11}]}]
        res = solve_weighted(classes, [0, 1], [1, 2], {1: 2, 2: 2})
        self.assertEqual(_hours_of(res, 1, "B"), 1)
        self.assertEqual(_hours_of(res, 1, "A"), 3)

    def test_no_one_hour_lesson_leaves_free_hour_as_unassigned(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 3, "teacher": 10}]}]
        res = solve_weighted(classes, [0, 1], [1, 2], {1: 2, 2: 2})
        # 4 bells of 2 hours = 8 hours of room; A uses 3; the other 5 hours are reported as unassigned
        self.assertEqual(sum(u["hours"] for u in res["unassigned"]), 5)

    def test_four_hour_lesson_by_one_teacher_in_one_day_when_allowed(self):
        # a teacher with 4 hours may teach in one day; the lesson's two-hour pieces may share that day
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 4, "teacher": 10}]}]
        res = solve_weighted(classes, [0, 1], [1, 2], {1: 2, 2: 2})
        self.assertEqual(_hours_of(res, 1, "A"), 4)
        days = {sl[0] for sl, lst in res["shared"][1].items() for (n, sz) in lst if n == "A"}
        self.assertEqual(len(days), 1)

    def test_teacher_is_not_in_two_classes_in_same_bell(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 2, "teacher": 10}]},
                   {"id": 2, "lessons": [{"name": "X", "hours": 2, "teacher": 10}]}]
        res = solve_weighted(classes, [0], [1], {1: 2})
        slots = [sl for cid in (1, 2) for sl in res["shared"].get(cid, {})]
        self.assertEqual(len(slots), len(set(slots)))

    def test_one_hour_bells_only_behaves_as_before(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 2, "teacher": 10}, {"name": "B", "hours": 1, "teacher": 11}]}]
        res = solve_weighted(classes, [0, 1], [1, 2, 3], {1: 1, 2: 1, 3: 1})
        self.assertEqual(_hours_of(res, 1, "A"), 2)
        self.assertEqual(_hours_of(res, 1, "B"), 1)
        # 6 one-hour slots, 3 hours of lessons: the other 3 slots are reported as free (فاقد درس)
        self.assertEqual(sum(u["hours"] for u in res["unassigned"]), 3)


    def test_teacher_with_ten_hours_teaches_in_at_most_two_days(self):
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 2, "teacher": 10}, {"name": "B", "hours": 2, "teacher": 10}]},
                   {"id": 2, "lessons": [{"name": "C", "hours": 2, "teacher": 10}, {"name": "D", "hours": 2, "teacher": 10}]},
                   {"id": 3, "lessons": [{"name": "E", "hours": 2, "teacher": 10}]}]
        res = solve_weighted(classes, [0, 1, 2, 3, 4], [1, 2, 3, 4], {1: 2, 2: 2, 3: 2, 4: 2})
        days = set()
        for cid, per in res["shared"].items():
            for (d, p), lst in per.items():
                days.add(d)
        self.assertLessEqual(len(days), 2)


    def test_teacher_with_six_hours_fills_one_day_before_another(self):
        # a teacher with 6 hours in two classes should be kept on one day when the bells allow it
        classes = [{"id": 1, "lessons": [{"name": "A", "hours": 2, "teacher": 10}, {"name": "B", "hours": 2, "teacher": 10}]},
                   {"id": 2, "lessons": [{"name": "C", "hours": 2, "teacher": 10}]}]
        res = solve_weighted(classes, [0, 1, 2], [1, 2, 3], {1: 2, 2: 2, 3: 2})
        days = {sl[0] for cid in (1, 2) for sl, lst in res["shared"].get(cid, {}).items() for (n, sz) in lst}
        self.assertEqual(len(days), 1)


if __name__ == "__main__":
    unittest.main()
