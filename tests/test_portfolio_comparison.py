import unittest
from datetime import date
from portfolio_comparison import compare_period,parse_daily_series,normalized_name,reconciliation_status

class PortfolioTests(unittest.TestCase):
    def test_daily_max_not_sum_and_midnight_exclusion(self):
        series={'unit':'kWh','resolved_route':'hist_daily','aggregation_resolved':'max',
                'points':[{'ts':'2026-10-24T03:00:00+00:00','value':1906},
                          {'ts':'2026-10-25T03:00:00+00:00','value':1462}]}
        self.assertEqual(parse_daily_series(series,date(2026,10,24),date(2026,10,24)),{date(2026,10,24):1906})
    def test_reject_wrong_resolution_duplicate_and_missing_timezone(self):
        base={'unit':'kWh','resolved_route':'hist_daily','aggregation_resolved':'max','points':[]}
        for update in [{'resolved_route':'hist_5min'},
                       {'points':[{'ts':'2026-10-24T03:00:00','value':1}]},
                       {'points':[{'ts':'2026-10-24T03:00:00+00:00','value':1}]*2}]:
            with self.assertRaises(ValueError):
                parse_daily_series({**base,**update},date(2026,10,24),date(2026,10,24))
    def test_group_partial_is_not_zero(self):
        day=date(2026,10,24)
        r=compare_period([('p','S1'),('q','S2')],{'p':[(1,10)],'q':[(1,11)]},
                         {(1,10):{day:100}}, {('p',day):98,('q',day):100},
                         {('p',10):3.1,('q',10):3.1},day,day)
        self.assertIsNone(r['aioti_kwh'])
        self.assertIsNone(r['aioti_minus_fabric_kwh'])
        self.assertEqual(r['fabric_kwh'],198)
        self.assertEqual(r['valid_unit_days']['aioti'],1)
    def test_zero_generation_valid_and_cross_month_forecast(self):
        a,b=date(2026,9,30),date(2026,10,1)
        r=compare_period([('p','SAP')],{'p':[(1,10)]},
                         {(1,10):{a:0,b:0}}, {('p',a):0,('p',b):0},
                         {('p',9):3,('p',10):6.2},a,b)
        self.assertEqual(r['aioti_kwh'],0)
        self.assertEqual(r['forecast_kwh'],300)
        self.assertEqual(r['fabric_vs_forecast_pct'],-100)
        self.assertIsNone(r['difference_pct_of_aioti'])
    def test_reconciliation_both_directions_and_missing(self):
        for gap,expected in [(6,'review_discrepancy'),(-6,'review_discrepancy'),(2,'within_screening_threshold')]:
            self.assertEqual(reconciliation_status({'aioti_kwh':100,'fabric_kwh':98,'difference_pct_of_aioti':gap}),expected)
        self.assertEqual(reconciliation_status({'aioti_kwh':None,'fabric_kwh':98,'difference_pct_of_aioti':None}),'data_gap')
        self.assertEqual(reconciliation_status({'aioti_kwh':0,'fabric_kwh':98,'difference_pct_of_aioti':None}),'zero_inverter_total_review')

    def test_names(self):
        self.assertEqual(normalized_name('Boa Viagem 2'),normalized_name('BOA VIAGEM II'))
        self.assertEqual(normalized_name('Santa Quitéria'),normalized_name('Santa Quiteria'))

if __name__=='__main__': unittest.main()
