"""Research checks: causal channels/N, fee accounting and gap-stop execution."""
import copy
import unittest

from turtle_hourly import channels, volatility, run, START, END, WARMUP, HOUR, INITIAL


class TurtleTests(unittest.TestCase):
    def fixture(self):
        bars = [{'ts': t, 'open': 100., 'high': 101., 'low': 99.,
                 'close': 100., 'volume': 1000.} for t in range(WARMUP, END, HOUR)]
        first = (START-WARMUP)//HOUR
        bars[first-1].update(high=105., close=104.)
        bars[first].update(open=104., high=105., low=103., close=104.)
        bars[first+1].update(open=98., high=100., low=97., close=99.)
        return bars, first

    def test_channel_excludes_signal_bar(self):
        bars, i = self.fixture()
        self.assertEqual(channels(bars, i-1, 55), (101., 99.))
        self.assertGreater(bars[i-1]['close'], channels(bars, i-1, 55)[0])

    def test_N_seed_and_recursion(self):
        bars, i = self.fixture()
        n = volatility(bars)
        self.assertEqual(n[19], 2.)
        self.assertAlmostEqual(n[i-1], (19*2+6)/20)
        changed = copy.deepcopy(bars)
        changed[i]['high'] = 1_000_000.
        self.assertEqual(volatility(changed)[:i], n[:i])

    def test_gap_stop_uses_open_and_reserves_buy_fee(self):
        bars, i = self.fixture()
        # Gap through 2N while staying inside the separate 5% portfolio guard.
        bars[i+1].update(open=99.7, high=100., low=99., close=99.8)
        r = run(bars, 's2_55_20')
        t = r['trade_log'][0]
        self.assertEqual(t['entry_ts'], START)
        self.assertEqual(t['exit_ts'], START+HOUR)
        self.assertEqual(t['reason'], '2N_gap_stop')
        self.assertAlmostEqual(t['exit_fill'], 99.7*.999)
        self.assertAlmostEqual(t['quantity']*t['entry_fill']*1.003, INITIAL)
        self.assertAlmostEqual(sum(x['pnl_tmn'] for x in r['trade_log']), r['net_pnl_tmn'])

    def test_future_data_cannot_change_first_entry(self):
        bars, i = self.fixture()
        before = run(bars, 's1_20_10_filtered')['trade_log'][0]
        bars[i+2]['high'] = 10_000.
        bars[i+2]['close'] = 9_000.
        after = run(bars, 's1_20_10_filtered')['trade_log'][0]
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
