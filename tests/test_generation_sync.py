import importlib.util
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

spec = importlib.util.spec_from_file_location('runner', Path(__file__).resolve().parents[1] / 'run_generation_sync.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class ValidationTests(unittest.TestCase):
    def test_sap_mapping_and_zero(self):
        self.assertEqual(runner.prepare_batch([('LATE', datetime(2026,10,1), 0)],
            {'LATE':'registered-id'}, date(2026,10,1), date(2026,10,2)),
            [('registered-id',date(2026,10,1),Decimal(0))])

    def test_reject_bad_values(self):
        for value in (None, -1, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.prepare_batch([('A',date(2026,10,1),value)], {'A':'1'},
                                     date(2026,10,1),date(2026,10,2))

    def test_reject_duplicate_and_unknown_and_out_of_range(self):
        row=('A',date(2026,10,1),1)
        for records in ([row,row],[('B',row[1],1)],[('A',date(2026,9,1),1)]):
            with self.assertRaises(ValueError):
                runner.prepare_batch(records, {'A':'1'},date(2026,10,1),date(2026,10,2))

if __name__ == '__main__':
    unittest.main()
