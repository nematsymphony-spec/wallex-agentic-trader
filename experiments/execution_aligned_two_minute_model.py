"""Same 2m feature model, label aligned to delayed actual TMN execution.

Fixed ridge penalty/features and chronological boundaries are retained. Only
the target changes: buy decision+60s, sell decision+180s. No hourly head.
"""
import hashlib
import json
import math
from pathlib import Path

from remaining_move_model import (dataset, select, fit, predict, metrics,
                                  FEATURES, LOCAL_ONLY, START, VALID, TEST, END, utc)
from two_minute_strategy import simulate, benchmark, SCENARIOS, DELAY, HOLD


def add_execution_labels(rows,prices,delay=DELAY,hold=HOLD):
    for row in rows:
        entry=row['available_at']+delay;exit_=entry+hold
        if entry in prices and exit_ in prices:
            row['yexec2']=math.log(prices[exit_]['open']/prices[entry]['open'])
            row['labelexec2_at']=exit_


def main():
    wp=Path('results/intraday-btc-pump-1m-data.json');fp=Path('results/leadlag-wallex-usdt-1m.json')
    raw=json.loads(wp.read_text())['symbols'];fx=json.loads(fp.read_text())['rows']
    output={'kind':'execution_aligned_two_minute_ml_candidate',
            'features':FEATURES,'train_start_utc':utc(START),'validation_start_utc':utc(VALID),
            'test_start_utc':utc(TEST),'test_end_utc':utc(END),
            'target':'TMN open-to-open log return: decision+60s entry to decision+180s exit',
            'model':'ridge, fixed penalty 10, per-coin standardized features',
            'allocation':'all available cash; independent BTC and PUMP accounts',
            'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (wp,fp)},
            'limitations':['Same historically explored test window, not untouched new data',
                           'One-minute simulated execution delay and synthetic fee/spread; actual fill prices and liquidity unknown',
                           'No credit daily fee or live orders','Cost sensitivity is not a search for a fee tier the account actually has'],
            'results':{}}
    models={}
    for symbol in ('BTC','PUMP'):
        rows,w,hashes=dataset(symbol,raw[symbol+'TMN'],fx)
        add_execution_labels(rows,w)
        train=select(rows,'exec2',START,VALID);valid=select(rows,'exec2',VALID,TEST)
        fit_rows=select(rows,'exec2',START,TEST);test=select(rows,'exec2',TEST,END)
        initial=fit(train,'exec2',list(range(len(FEATURES))))
        final=fit(fit_rows,'exec2',list(range(len(FEATURES))))
        local=fit(fit_rows,'exec2',LOCAL_ONLY)
        assert initial['max_label_at']<VALID and final['max_label_at']<TEST
        validation=metrics(initial,valid,'exec2');tested=metrics(final,test,'exec2')
        predictions={r['available_at']:{'prediction':predict(final,r),'feature_ref':r['candle_ts']}
                     for r in rows if TEST<=r['available_at']<END}
        passed=validation['r2_vs_zero']>0
        result={'training_rows':len(train),'validation_rows':len(valid),'test_rows':len(test),
                'binance_hashes':hashes,'validation':validation,'test':tested,
                'test_local_only':metrics(local,test,'exec2'),
                'model_gate_passed_validation':passed,
                'deployment_status':('rejected_by_validation' if not passed else
                                     'rejected_by_evaluation' if tested['r2_vs_zero']<=0 else
                                     'requires_fresh_data_fee_and_execution_validation'),
                'model':final,'scenarios':{},
                'feature_snapshots':{str(r['candle_ts']):dict(zip(FEATURES,r['x'])) for r in rows if TEST<=r['available_at']<END}}
        for name,fee,half in SCENARIOS:
            r=simulate(predictions,w,fee,half,trace=name==SCENARIOS[0][0])
            r['buy_hold']=benchmark(w,fee,half)
            r['validation_approved']=passed
            result['scenarios'][name]=r
            print(symbol,name,'return',round(r['net_return_pct'],4),'trades',r['trades'])
        output['results'][symbol]=result;models[symbol]=final
        print(symbol,'validation R2',round(validation['r2_vs_zero'],4),'test R2',round(tested['r2_vs_zero'],4))
    Path('results/execution-aligned-two-minute-btc-pump.json').write_text(json.dumps(output,indent=2)+'\n')
    Path('results/execution-aligned-two-minute-models.json').write_text(json.dumps({'features':FEATURES,
        'delay_seconds':DELAY,'hold_seconds':HOLD,'models':models},indent=2)+'\n')


if __name__=='__main__':
    main()
