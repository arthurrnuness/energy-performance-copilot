import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from daily_generation_sync import due_slot

class ScheduleTests(unittest.TestCase):
    def test_due_slot_boundaries(self):
        for stamp, expected in [
            ('2026-10-26T09:29:59','2026-10-25'),
            ('2026-10-26T09:30:00','2026-10-26'),
            ('2026-10-26T18:00:00','2026-10-26'),
            ('2026-11-01T08:00:00','2026-10-31'),
        ]:
            with self.subTest(stamp=stamp):
                now=datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('America/Fortaleza'))
                self.assertEqual(due_slot(now),expected)

    def test_timezone_independent(self):
        now=datetime.fromisoformat('2026-10-26T12:30:00+00:00')
        self.assertEqual(due_slot(now),'2026-10-26')
