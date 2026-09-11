import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from spectra_agent.dingtalk_push import weekend_delivery_blocked

class WeekendDeliveryTest(unittest.TestCase):
    def test_weekend_and_old_weekend_issues_never_send(self):
        monday = datetime(2026,9,14,10,30,tzinfo=ZoneInfo('Asia/Shanghai'))
        saturday = datetime(2026,9,12,10,30,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertTrue(weekend_delivery_blocked('daily-20260912', saturday))
        self.assertTrue(weekend_delivery_blocked('daily-20260911', saturday))
        self.assertTrue(weekend_delivery_blocked('daily-20260912', monday))
        self.assertFalse(weekend_delivery_blocked('daily-20260914', monday))
