import copy
import math
import unittest
import numpy as np
from research30 import Research, START, END, WARMUP, HOUR, INITIAL, features
from research30_books import measure


class ResearchAccountingTests(unittest.TestCase):
    def fixture(self):
        r=Research.__new__(Research)
        n=(END-WARMUP)//HOUR
        a=np.tile(np.array([100.,101.,99.,100.,1000.]),(n,1))
        r.bars={'TESTTMN':{'a':a,'real':np.ones(n,dtype=bool)}}
        r.f={'TESTTMN':{'atr':np.ones(n)}};r.prec={'TESTTMN':8}
        r.choose=lambda cfg,i:'TESTTMN'
        c={'id':'test','family':'test','hold':10000,'stop_kind':'fixed','stop':.05,'tp':0.}
        return r,c,(START-WARMUP)//HOUR

    def test_flat_roundtrip_reserves_fee_and_uses_two_sides(self):
        r,c,i=self.fixture();x=r.simulate(c,trace=True)
        expected=((1-.003)/(1+.003)*(.999/1.001)-1)*100
        self.assertAlmostEqual(x['return_pct'],expected,places=7)
        self.assertEqual(x['trades'],1)
        t=x['trade_log'][0]
        self.assertLessEqual(t['qty']*t['entry_fill']+t['buy_fee_tmn'],INITIAL)
        self.assertAlmostEqual(sum(z['pnl_tmn'] for z in x['trade_log']),x['pnl_tmn'])

    def test_profit_goal_is_net_of_fees_and_stops_future_buys(self):
        r,c,i=self.fixture();c['profit_goal']=.03
        r.bars['TESTTMN']['a'][i,1]=110.
        x=r.simulate(c,trace=True)
        self.assertTrue(x['profit_goal_hit']);self.assertEqual(x['trades'],1)
        self.assertAlmostEqual(x['return_pct'],3.,places=9)
        self.assertAlmostEqual(x['final_tmn'],INITIAL*1.03,places=6)

    def test_periodic_goal_compounds_and_resumes_next_block(self):
        r,c,i=self.fixture();c.update(profit_goal=.03,goal_reset_hours=24)
        r.bars['TESTTMN']['a'][:,1]=110.
        x=r.simulate(c,end=START+48*3600,trace=True)
        self.assertEqual(x['trades'],2)
        self.assertEqual(len(x['monthly_blocks']),2)
        self.assertAlmostEqual(x['return_pct'],(1.03**2-1)*100,places=8)
        self.assertTrue(all(b['profit_goal_hit']for b in x['monthly_blocks']))

    def test_global_loss_halt_persists_but_block_budget_can_reset(self):
        r,c,i=self.fixture();c.update(profit_goal=.03,goal_reset_hours=24)
        r.bars['TESTTMN']['a'][:,2]=90.
        global_halt=r.simulate(c,end=START+48*3600)
        monthly_halt=r.simulate({**c,'portfolio_loss_reset':'block'},end=START+48*3600)
        self.assertEqual(global_halt['trades'],1)
        self.assertEqual(monthly_halt['trades'],2)
        self.assertLess(monthly_halt['return_pct'],global_halt['return_pct'])

    def test_intrahour_ambiguity_is_not_silently_chosen(self):
        r,c,i=self.fixture();c.update(tp=.05,profit_goal=.03)
        r.bars['TESTTMN']['a'][i,1:3]=[110.,90.]
        a=r.simulate(c,path='OLHC');b=r.simulate(c,path='OHLC')
        self.assertLess(a['return_pct'],0)
        self.assertAlmostEqual(b['return_pct'],3.,places=9)

    def test_zero_volume_bar_cannot_be_an_entry(self):
        r,c,i=self.fixture();r.bars['TESTTMN']['real'][i]=False
        day=START//86400*86400
        r.top={day:['TESTTMN']}
        r.screens={day:[{'symbol':'TESTTMN','historical_proxy_score':99.,'return3':.1}]}
        r.f['TESTTMN']['r72']=np.ones(len(r.bars['TESTTMN']['a']))
        r.choose=Research.choose.__get__(r,Research)
        c.update(family='volatility_control',positive=False)
        self.assertIsNone(r.choose(c,i))
        r.bars['TESTTMN']['real'][i]=True
        self.assertEqual(r.choose(c,i),'TESTTMN')

    def test_feature_prefix_invariant_to_future_prices(self):
        r,c,i=self.fixture();before=features(r.bars)['TESTTMN']
        r.bars['TESTTMN']['a'][i+20:,0:4]*=2
        after=features(r.bars)['TESTTMN']
        for key in before:
            np.testing.assert_allclose(before[key][:i+20],after[key][:i+20],equal_nan=True)

    def test_book_cost_does_not_double_count_spread(self):
        book={'ask':[{'price':101.,'quantity':100000.}],
              'bid':[{'price':99.,'quantity':100000.}]}
        x=measure(book,5_000_000,fee=.003)
        expected=(1-99/101*(1-.003)/(1+.003))*100
        self.assertTrue(x['valid'])
        self.assertAlmostEqual(x['roundtrip_cost_pct'],expected)
        self.assertAlmostEqual(x['spread_depth_only_cost_pct'],(1-99/101)*100)


if __name__=='__main__':unittest.main()
