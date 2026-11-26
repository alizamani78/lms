from django.test import SimpleTestCase

from apps.timetable.solver import check_grid, solve

DAYS = [0, 1, 2, 3, 4]
PERIODS = [1, 2, 3, 4]


def L(i, name, teacher, hours, diff="medium", workshop=False):
    return {"id": i, "name": name, "teacher": teacher, "hours": hours, "difficulty": diff, "workshop": workshop}


class SolverTests(SimpleTestCase):
    def test_simple_week_is_feasible_and_respects_rules(self):
        classes = [{"id": 1, "name": "۱۰۱", "lessons": [L(10, "ریاضی", 100, 3, "hard"), L(11, "فارسی", 101, 2, "easy")]},
                   {"id": 2, "name": "۱۰۲", "lessons": [L(10, "ریاضی", 100, 3, "hard"), L(12, "علوم", 102, 2, "medium")]}]
        r = solve(classes, DAYS, PERIODS)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(check_grid(classes, r["grid"]), [])

    def test_teacher_is_never_in_two_classes_at_once(self):
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "ریاضی", 7, 4)]},
                   {"id": 2, "name": "B", "lessons": [L(2, "ریاضی", 7, 4)]}]
        r = solve(classes, DAYS, PERIODS)
        self.assertTrue(r["ok"])
        seen = set()
        for cid, cells in r["grid"].items():
            for s in cells:
                self.assertNotIn(s, seen)
                seen.add(s)

    def test_each_lesson_once_per_day(self):
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "ریاضی", 7, 3), L(2, "فارسی", 8, 3)]}]
        r = solve(classes, DAYS, PERIODS)
        self.assertTrue(r["ok"])
        for lid in (1, 2):
            days = [s[0] for s, x in r["grid"][1].items() if x == lid]
            self.assertEqual(len(days), len(set(days)))

    def test_teacher_availability_is_respected(self):
        free = {7: {(0, 1), (1, 2), (2, 3)}}
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "ریاضی", 7, 3)]}]
        r = solve(classes, DAYS, PERIODS, teacher_free=free)
        self.assertTrue(r["ok"], r["errors"])
        self.assertTrue(set(r["grid"][1]).issubset(free[7]))

    def test_not_enough_teacher_hours_gives_the_reason(self):
        free = {7: {(0, 1)}}
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "ریاضی", 7, 3)]}]
        r = solve(classes, DAYS, PERIODS, teacher_free=free)
        self.assertFalse(r["ok"])
        self.assertTrue(any("ساعت حضور کافی ندارد" in e for e in r["errors"]))

    def test_workshop_uses_a_room_and_conflicts_are_detected(self):
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "کارگاه", 7, 2, workshop=True)]},
                   {"id": 2, "name": "B", "lessons": [L(2, "کارگاه", 8, 2, workshop=True)]}]
        r = solve(classes, DAYS, PERIODS, workshop_rooms=1)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(check_grid(classes, r["grid"], workshop_rooms=1), [])
        bad = {1: {(0, 1): 1}, 2: {(0, 1): 2}}
        self.assertTrue(any("تداخل کارگاه" in p for p in check_grid(classes, bad, workshop_rooms=1)))

    def test_too_many_hours_is_refused_with_reason(self):
        classes = [{"id": 1, "name": "A", "lessons": [L(i, f"د{i}", 7, 4) for i in range(6)]}]
        r = solve(classes, DAYS, [1])
        self.assertFalse(r["ok"])
        self.assertTrue(r["errors"])


class PlannerRulesTests(SimpleTestCase):
    """The approval gate and the hard-lesson difficulty are plain rules (no database needed here)."""

    def test_text_levels_become_numbers_and_hard_lessons_are_recognised(self):
        from apps.timetable.solver import is_hard, level_of
        self.assertEqual(level_of({"difficulty": "hard"}), 5)
        self.assertEqual(level_of({"difficulty": 4}), 4)
        self.assertTrue(is_hard({"difficulty": 5}))
        self.assertFalse(is_hard({"difficulty": 3}))

    def test_solver_spreads_a_hard_lesson_across_days(self):
        from apps.timetable.solver import solve
        classes = [{"id": 1, "name": "A", "lessons": [L(1, "ریاضی", 7, 3, 5), L(2, "فارسی", 8, 3, 1)]}]
        r = solve(classes, DAYS, PERIODS)
        self.assertTrue(r["ok"])
        days = [s[0] for s, x in r["grid"][1].items() if x == 1]
        self.assertEqual(len(set(days)), 3)
