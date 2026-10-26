import unittest
from datetime import datetime, timedelta, timezone
from investigation_tools import summarize_energy_counter

class CounterTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,10,24,tzinfo=timezone.utc)
        self.end=self.start+timedelta(days=1)
    def series(self, values, step=5):
        return {'unit':'kWh','points':[{'ts':(self.start+timedelta(minutes=i*step)).isoformat(),'value':v} for i,v in enumerate(values)]}
    def test_increment_and_zero(self):
        for values, expected in [([100,105,112],12),([100,100],0)]:
            self.assertEqual(summarize_energy_counter(self.series(values),self.start,self.end)['estimated_kwh'],expected)
    def test_reset_gap_missing_invalid(self):
        for series in [None,self.series([10,9]),self.series([10,12],30),self.series([float('nan'),12]),self.series([12])]:
            self.assertIsNone(summarize_energy_counter(series,self.start,self.end)['estimated_kwh'])
    def test_exclude_next_day(self):
        s=self.series([10,12]); s['points'].append({'ts':self.end.isoformat(),'value':999})
        self.assertEqual(summarize_energy_counter(s,self.start,self.end)['estimated_kwh'],2)
