"""Checks for execution timing, all-in costs and invalid-signal rejection."""
import math
import unittest

from two_minute_strategy import simulate, cost_hurdle, INITIAL
from execution_aligned_two_minute_model import add_execution_labels
from remaining_move_model import select


class TwoMinuteExecutionTests(unittest.TestCase):
    def setUp(self):
        self.start=1792465200
        self.end=self.start+600
        self.prices={t:{'open':1.0,'close':1.0,'volume':1.0}
                     for t in range(self.start,self.end,60)}

    def predictions(self,first):
        return {t:{'prediction':first if t==self.start else .00001,'feature_ref':t-60}
                for t in self.prices}

    def test_all_in_fees_and_two_minutes_after_delayed_fill(self):
        result=simulate(self.predictions(.02),self.prices,.003,.001,
                        start=self.start,end=self.end,trace=True)
        self.assertEqual(result['trades'],1)
        trade=result['trade_log'][0]
        self.assertEqual(trade['decision_at'],self.start)
        self.assertEqual(trade['entry_ts'],self.start+60)
        self.assertEqual(trade['exit_ts'],self.start+180)
        expected=((1-.003)/(1+.003)*(1-.001)/(1+.001)-1)*100
        self.assertAlmostEqual(result['net_return_pct'],expected,places=9)
        self.assertAlmostEqual(trade['pnl_tmn'],result['net_pnl_tmn'],places=8)
        self.assertAlmostEqual(trade['quantity']*trade['entry_fill']+trade['entry_fee'],INITIAL,places=8)

    def test_fill_uses_future_execution_price_not_signal_price(self):
        for t in range(self.start+60,self.end,60):
            self.prices[t].update(open=2.0,close=2.0)
        result=simulate(self.predictions(.02),self.prices,0,0,
                        start=self.start,end=self.end)
        self.assertEqual(result['trades'],1)
        self.assertEqual(result['trade_log'][0]['entry_fill'],2.0)
        self.assertAlmostEqual(result['net_return_pct'],0.0)

    def test_invalid_or_below_hurdle_never_buys(self):
        for value in (float('nan'),float('inf'),cost_hurdle(.003,.001)-1e-10):
            result=simulate(self.predictions(value),self.prices,.003,.001,
                            start=self.start,end=self.end)
            self.assertEqual(result['trades'],0)
            self.assertEqual(result['final_equity_tmn'],INITIAL)

    def test_execution_label_crossing_boundary_is_not_training(self):
        rows=[{'available_at':self.start,'x':[]}]
        add_execution_labels(rows,self.prices)
        self.assertEqual(rows[0]['labelexec2_at'],self.start+180)
        self.assertEqual(select(rows,'exec2',self.start,self.start+180),[])
        self.assertEqual(len(select(rows,'exec2',self.start,self.start+181)),1)


if __name__=='__main__':
    unittest.main()
