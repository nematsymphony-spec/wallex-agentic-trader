"""Research integrity checks; run separately from production unit tests."""
import copy
import json
import math
import unittest
from pathlib import Path

import remaining_move_model as model


class RemainingMoveIntegrityTests(unittest.TestCase):
    def test_labels_crossing_split_are_purged(self):
        rows=[{'available_at':100,'y2':1,'label2_at':220},
              {'available_at':120,'y2':2,'label2_at':180},
              {'available_at':200,'y2':3,'label2_at':250}]
        chosen=model.select(rows,2,0,200)
        self.assertEqual([r['available_at'] for r in chosen],[120])

    def test_one_all_in_roundtrip_matches_independent_cost_formula(self):
        prices={t:{'open':1.0,'close':1.0,'volume':1.0}
                for t in range(model.TEST,model.END,60)}
        row={'available_at':model.TEST,'candle_ts':model.TEST-60,'x':[0.0]*len(model.FEATURES)}
        def constant(value):
            return {'intercept':value,'columns':[],'mean':[],'scale':[],'weights':[]}
        models={'2':constant(.01),'60':constant(.02)}
        result=model.simulate([row],models,prices)
        expected=((1-model.FEE)/(1+model.FEE)*(1-model.HALF_SPREAD)/(1+model.HALF_SPREAD)-1)*100
        self.assertEqual(result['trades'],1)
        self.assertAlmostEqual(result['net_return_pct'],expected,places=9)
        self.assertEqual(result['trade_log'][0]['entry_ts'],model.TEST+60)
        self.assertEqual(result['trade_log'][0]['exit_ts'],model.TEST+3660)

    def test_future_local_prices_do_not_change_current_features(self):
        raw=json.loads(Path('results/intraday-btc-pump-1m-data.json').read_text())['symbols']['BTCTMN']
        fx=json.loads(Path('results/leadlag-wallex-usdt-1m.json').read_text())['rows']
        original,_,_=model.dataset('BTC',raw,fx)
        altered=copy.deepcopy(raw)
        cutoff=model.TEST+3600
        for r in altered:
            if r['timestamp']>cutoff:
                r['close']=str(float(r['close'])*2)
                r['open']=str(float(r['open'])*2)
        changed,_,_=model.dataset('BTC',altered,fx)
        before={r['candle_ts']:r['x'] for r in original if r['candle_ts']<=cutoff}
        after={r['candle_ts']:r['x'] for r in changed if r['candle_ts']<=cutoff}
        self.assertEqual(before,after)


if __name__=='__main__':
    unittest.main()
