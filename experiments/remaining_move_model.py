"""Chronological ridge models of remaining Wallex movement; no live orders.

Two-minute FX-adjusted head measures catch-up; 60-minute TMN head gates a
long-only hourly simulation. Cached public candles and numpy are required.
The evaluation period was previously explored: chronological holdout is only
with respect to fitting, not an untouched research holdout.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import statistics
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from cross_venue_leadlag import load_binance, corr

START = 1792238400  # Oct 17 12 UTC
VALID = 1792368000  # Oct 19 00 UTC
TEST = 1792465200  # Oct 20 03 UTC
END = 1792627200  # Oct 22 00 UTC; Binance latest archived day
INITIAL = 5_000_000.0
FEE = .003
HALF_SPREAD = .001
BUFFER = .002  # 20 bps beyond assumed round-trip execution cost
FEATURES = ['binance_1m', 'binance_previous_1m', 'local_usdt_1m',
            'local_usdt_previous_1m', 'relative_discount_vs_prior_hour',
            'fx_1m', 'local_usdt_1h', 'binance_1h', 'local_1h_volatility',
            'local_volume_ratio']
LOCAL_ONLY = [2, 3, 5, 6, 8, 9]


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def dataset(symbol, wraw, fraw):
    b, hashes = load_binance(symbol)
    w = {r['timestamp']: {k: float(r[k]) for k in ('open', 'close', 'volume')} for r in wraw}
    fx = {r['timestamp']: float(r['close']) for r in fraw}
    adj = {t: math.log(r['close']/fx[t]) for t,r in w.items() if t in fx}
    blog = {t: math.log(r['close']) for t,r in b.items()}
    out = []
    for t in range(START+3600, END, 60):
        # Only complete, contiguous past observations; no interpolation.
        past = list(range(t-3600, t+60, 60))
        if any(s not in adj or s not in blog for s in past):
            continue
        old_basis = [blog[s]-adj[s] for s in past[:-1]]
        local_returns = [adj[s]-adj[s-60] for s in past[1:]]
        median_volume = statistics.median(w[s]['volume'] for s in past[:-1])
        x = [blog[t]-blog[t-60], blog[t-60]-blog[t-120],
             adj[t]-adj[t-60], adj[t-60]-adj[t-120],
             blog[t]-adj[t]-statistics.median(old_basis),
             math.log(fx[t]/fx[t-60]), adj[t]-adj[t-3600],
             blog[t]-blog[t-3600], statistics.pstdev(local_returns),
             math.log1p(w[t]['volume']/(median_volume+1e-12))]
        row = {'candle_ts':t,'available_at':t+60,'x':x}
        # t+60 is when the feature candle closes. Labels never cross split
        # boundaries during fitting/evaluation. Head 60 includes one minute
        # simulated execution delay, then a full 60-minute holding interval.
        if t+120 in adj:
            row['y2'] = adj[t+120]-adj[t]
            row['label2_at'] = t+180
        entry_ts = t+120  # available_at + 60 seconds, next minute open
        exit_ts = entry_ts+3600
        if entry_ts in w and exit_ts in w:
            row['y60'] = math.log(w[exit_ts]['open']/w[entry_ts]['open'])
            row['label60_at'] = exit_ts
        out.append(row)
    return out, w, hashes


def select(rows, head, left, right):
    return [r for r in rows if left <= r['available_at'] < right and
            f'y{head}' in r and r[f'label{head}_at'] < right]


def fit(rows, head, columns):
    x = np.array([[r['x'][i] for i in columns] for r in rows], dtype=float)
    y = np.array([r[f'y{head}'] for r in rows], dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1
    z = (x-mean)/scale
    ymean = float(y.mean())
    if not np.isfinite(z).all() or not np.isfinite(y).all():
        raise ValueError('non-finite model inputs')
    # Explicit reductions avoid spurious matmul floating-point warnings from
    # the host's BLAS backend; all standardized inputs are checked above.
    gram = np.einsum('ni,nj->ij',z,z)
    rhs = np.einsum('ni,n->i',z,y-ymean)
    weights = np.linalg.solve(gram+10*np.eye(len(columns)), rhs)
    if not np.isfinite(weights).all():
        raise ValueError('non-finite fitted weights')
    return {'columns':columns,'mean':mean.tolist(),'scale':scale.tolist(),
            'weights':weights.tolist(),'intercept':ymean,'ridge_penalty':10.0,
            'fitted_rows':len(rows),'max_label_at':max(r[f'label{head}_at'] for r in rows)}


def predict(model, row):
    return model['intercept']+sum((row['x'][i]-m)/s*w for i,m,s,w in zip(
        model['columns'],model['mean'],model['scale'],model['weights']))


def metrics(model, rows, head):
    actual = np.array([r[f'y{head}'] for r in rows])
    pred = np.array([predict(model,r) for r in rows])
    mse = float(np.mean((actual-pred)**2))
    zero = float(np.mean(actual**2))
    nonzero = actual != 0
    return {'n':len(rows),'mae_bps':float(np.mean(abs(actual-pred)))*10000,
            'nonzero_targets':int(nonzero.sum()),
            'zero_forecast_mae_bps':float(np.mean(abs(actual)))*10000,
            'rmse_bps':math.sqrt(mse)*10000,'zero_forecast_rmse_bps':math.sqrt(zero)*10000,
            'r2_vs_zero':1-mse/zero if zero else None,
            'prediction_actual_correlation':corr(list(zip(pred.tolist(),actual.tolist()))),
            'direction_accuracy_nonzero_pct':float(np.mean(pred[nonzero]*actual[nonzero]>0))*100,
            'always_up_accuracy_nonzero_pct':float(np.mean(actual[nonzero]>0))*100,
            'max_prediction_pct':100*math.expm1(float(pred.max()))}


def simulate(rows, models, w, fee=FEE, half=HALF_SPREAD):
    predictions = {r['available_at']:r for r in rows if TEST <= r['available_at'] < END}
    # Exact price ratio needed to recover execution costs, plus fixed buffer.
    hurdle = math.log((1+fee)/(1-fee)*(1+half)/(1-half))+BUFFER
    cash, qty, basis, entered = INITIAL, 0.0, 0.0, None
    peak, dd, fees = INITIAL, 0.0, 0.0
    day, day_start, daily_stop, total_stop = None, INITIAL, False, False
    trades, decisions = [], []
    def sell(price,ts,reason):
        nonlocal cash,qty,fees
        proceeds = qty*price*(1-half)
        f = proceeds*fee
        cash += proceeds-f
        fees += f
        trades.append({'entry_ts':entered,'exit_ts':ts,'pnl_tmn':proceeds-f-basis,'reason':reason})
        qty=0.0
    for hour in range(TEST,END,3600):
        execute=hour+60
        if execute not in w:
            raise ValueError('missing execution price')
        price=w[execute]['open']
        marked=cash+qty*price*(1-half)*(1-fee)
        today=datetime.fromtimestamp(execute,ZoneInfo('Asia/Tehran')).date()
        if today != day:
            day,day_start,daily_stop=today,marked,False
        daily_stop |= marked<=day_start*.98
        total_stop |= marked<=INITIAL*.95
        if qty:
            sell(price,execute,'one_hour_exit')
        row=predictions.get(hour)
        decision={'available_at':hour,'execute_at':execute,'action':'HOLD','reason':'incomplete_feature_history'}
        if row:
            p2,p60=predict(models['2'],row),predict(models['60'],row)
            decision.update({'predicted_2m_log_return':p2,'predicted_60m_log_return':p60,
                             'required_60m_log_return':hurdle,'feature_candle_ts':row['candle_ts'],
                             'features':dict(zip(FEATURES,row['x']))})
            reason='short_signal_nonpositive' if p2<=0 else 'below_cost_hurdle' if p60<=hurdle else 'risk_stop' if daily_stop or total_stop else None
            if reason is None:
                size=cash/(1+fee)
                buyfee=size*fee
                qty=size/(price*(1+half));basis=size+buyfee;entered=execute
                cash-=basis;fees+=buyfee
                decision.update({'action':'BUY','reason':'both_heads_pass_cost_and_risk'})
            else:
                decision['reason']=reason
        decisions.append(decision)
        # Risk monitoring uses actual 1m local trade candles, next-open exits.
        for t in range(execute,hour+3600,60):
            if t not in w:
                raise ValueError('missing marking candle')
            minute_day=datetime.fromtimestamp(t,ZoneInfo('Asia/Tehran')).date()
            if minute_day != day:
                day=minute_day
                day_start=cash+qty*w[t]['open']*(1-half)*(1-fee)
                daily_stop=False
            equity=cash+qty*w[t]['close']*(1-half)*(1-fee)
            peak=max(peak,equity);dd=max(dd,1-equity/peak)
            daily_stop |= equity<=day_start*.98
            total_stop |= equity<=INITIAL*.95
            if qty and total_stop and t+60 in w:
                sell(w[t+60]['open'],t+60,'total_stop_next_minute')
    if qty:
        sell(w[END-60]['close'],END,'window_end')
    return {'net_return_pct':100*(cash/INITIAL-1),'net_pnl_tmn':cash-INITIAL,
            'final_equity_tmn':cash,'max_drawdown_pct':100*dd,'trades':len(trades),
            'fees_tmn':fees,'total_stop_triggered':total_stop,'trade_log':trades,
            'decision_log':decisions}


def benchmark(w, fee=FEE, half=HALF_SPREAD):
    qty=INITIAL/(1+fee)/(w[TEST+60]['open']*(1+half))
    final=qty*w[END-60]['close']*(1-half)*(1-fee)
    peak,dd=INITIAL,0
    for t in range(TEST+60,END,60):
        equity=qty*w[t]['close']*(1-half)*(1-fee)
        peak=max(peak,equity);dd=max(dd,1-equity/peak)
    return {'net_return_pct':100*(final/INITIAL-1),'net_pnl_tmn':final-INITIAL,
            'max_drawdown_pct':100*dd,'entry_ts':TEST+60,'exit_ts':END}


def main():
    wp=Path('results/intraday-btc-pump-1m-data.json');fp=Path('results/leadlag-wallex-usdt-1m.json')
    wraw=json.loads(wp.read_text())['symbols'];fraw=json.loads(fp.read_text())['rows']
    output={'kind':'exploratory_trained_remaining_move_models_and_hourly_backtest',
            'features':FEATURES,'train_start_utc':utc(START),'validation_start_utc':utc(VALID),
            'test_start_utc':utc(TEST),'test_end_utc':utc(END),
            'environment':{'python':platform.python_version(),'numpy':np.__version__},
            'assumptions':{'allocation':'all available cash, one coin per independent account','initial_tmn':INITIAL,
                           'fee_each_side':FEE,'full_spread_bps':HALF_SPREAD*20000,'extra_entry_buffer_bps':BUFFER*10000,
                           'decision_cadence':'hourly','execution_delay_seconds':60,'maximum_hold_seconds':3600,
                           'risk':'2% Tehran-day new-buy pause; 5% initial-capital loss stop checked on minute closes',
                           'historical_depth':'unavailable; execution assumed possible','credit_daily_fee':'not included'},
            'limitations':['Test window previously used for lead-lag discovery: not an untouched research holdout',
                           'Chronological split purges labels crossing boundary, but only a few days exist',
                           'Trade candles cannot validate historical spread, queue, latency or liquidity',
                           'No actual account fees or credit fee; no live orders'],
            'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (wp,fp)},'results':{}}
    models_artifact={}
    for symbol in ('BTC','PUMP'):
        rows,w,hashes=dataset(symbol,wraw[symbol+'TMN'],fraw)
        result={'binance_hashes':hashes,'heads':{}}
        final_models={}
        for head in (2,60):
            train=select(rows,head,START,VALID);valid=select(rows,head,VALID,TEST)
            fit_rows=select(rows,head,START,TEST);test=select(rows,head,TEST,END)
            full=fit(train,head,list(range(len(FEATURES))))
            final=fit(fit_rows,head,list(range(len(FEATURES))))
            local=fit(fit_rows,head,LOCAL_ONLY)
            assert final['max_label_at']<TEST
            result['heads'][str(head)]={'training_rows':len(train),'validation_rows':len(valid),'test_rows':len(test),
                'validation':metrics(full,valid,head),'test_full':metrics(final,test,head),
                'test_local_only':metrics(local,test,head),'final_model':final}
            final_models[str(head)]=final
        result['validation_gate']={'required':'Each head must beat zero-forecast MSE on validation',
            'head_2_pass':result['heads']['2']['validation']['r2_vs_zero']>0,
            'head_60_pass':result['heads']['60']['validation']['r2_vs_zero']>0}
        result['validation_gate']['hourly_strategy_accepted']=all(result['validation_gate'][k] for k in ('head_2_pass','head_60_pass'))
        result['deployment_decision']={'action':'HOLD',
            'reason':'hourly_head_failed_validation' if not result['validation_gate']['hourly_strategy_accepted'] else 'live_account_route_not_verified'}
        result['strategy']=simulate(rows,final_models,w)
        result['buy_hold']=benchmark(w)
        result['zero_cost_diagnostic']=simulate(rows,final_models,w,fee=0,half=0)
        result['strategy']['validation_status']='approved' if result['validation_gate']['hourly_strategy_accepted'] else 'rejected_model_diagnostic_only'
        result['zero_cost_diagnostic']['validation_status']='rejected_model_diagnostic_only' if not result['validation_gate']['hourly_strategy_accepted'] else 'approved'
        output['results'][symbol]=result
        models_artifact[symbol]=final_models
        print(symbol)
        for head in ('2','60'):
            a=result['heads'][head]['test_full'];base=result['heads'][head]['test_local_only']
            print(head,'n',a['n'],'R2_zero',round(a['r2_vs_zero'],4),'local_R2',round(base['r2_vs_zero'],4),'corr',round(a['prediction_actual_correlation'],4))
        for kind in ('strategy','buy_hold','zero_cost_diagnostic'):
            r=result[kind];print(kind,round(r['net_return_pct'],4),'trades',r.get('trades'))
    root=Path('results')
    (root/'remaining-move-btc-pump.json').write_text(json.dumps(output,indent=2)+'\n')
    (root/'remaining-move-models.json').write_text(json.dumps({'features':FEATURES,'models':models_artifact},indent=2)+'\n')


if __name__=='__main__':
    main()
