"""Frozen 2m model -> causal minute-by-minute all-in execution simulation.

No hourly head, no model refit, no exchange order code. Prediction horizon is
FX-adjusted; realized account P&L is TMN. Explicit latency/holding mismatch is
measured rather than hidden. All cost scenarios are diagnostics, not fee claims.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

import numpy as np

from remaining_move_model import (dataset, predict, FEATURES, TEST, END, INITIAL, utc)

HOLD = 120
DELAY = 60
BUFFER = .002
SCENARIOS = [('fee_30bps_spread_20bps',.003,.001),
             ('fee_20bps_spread_20bps',.002,.001),
             ('fee_10bps_spread_20bps',.001,.001),
             ('zero_fee_spread_20bps',0.0,.001),
             ('zero_cost',0.0,0.0)]


def cost_hurdle(fee, half, buffer=BUFFER):
    return math.log((1+fee)/(1-fee)*(1+half)/(1-half))+buffer


def simulate(predictions, prices, fee, half, delay=DELAY, hold=HOLD,
             start=TEST, end=END, buffer=BUFFER, trace=False):
    if delay not in (0,60) or hold<60 or hold%60:
        raise ValueError('simulation requires minute-aligned supported timings')
    hurdle=cost_hurdle(fee,half,buffer)
    cash,qty,basis=INITIAL,0.0,0.0
    pending,position=None,None
    peak,dd,fees=INITIAL,0.0,0.0
    day,day_start,daily_stop,total_stop=None,INITIAL,False,False
    trades,decisions,counts,curve=[],[],Counter(),[]

    def buy(ts,order):
        nonlocal cash,qty,basis,position,fees
        size=cash/(1+fee)
        fill=prices[ts]['open']*(1+half)
        entry_fee=size*fee
        qty=size/fill;basis=size+entry_fee;cash-=basis;fees+=entry_fee
        position={'entry_ts':ts,'entry_fill':fill,'entry_fee':entry_fee,
                  'decision_at':order['decision_at'],'predicted_log_return':order['prediction'],
                  'quantity':qty,'deadline':ts+hold,'exit_at':None,'exit_reason':None}

    def sell(ts,gross,reason):
        nonlocal cash,qty,position,fees
        fill=gross*(1-half)
        proceeds=qty*fill;exit_fee=proceeds*fee
        cash+=proceeds-exit_fee;fees+=exit_fee
        trades.append({**position,'exit_ts':ts,'exit_fill':fill,'exit_fee':exit_fee,
                       'reason':reason,'pnl_tmn':proceeds-exit_fee-basis})
        qty=0.0;position=None

    for ts in range(start,end,60):
        if ts not in prices:
            raise ValueError('missing actual local candle; simulation cannot infer fills')
        bar=prices[ts]
        marked_open=cash+qty*bar['open']*(1-half)*(1-fee)
        today=datetime.fromtimestamp(ts,ZoneInfo('Asia/Tehran')).date()
        if today!=day:
            day,day_start,daily_stop=today,marked_open,False
        daily_stop |= marked_open<=day_start*.98
        total_stop |= marked_open<=INITIAL*.95

        if position:
            due=position['exit_at'] is not None and ts>=position['exit_at']
            if total_stop or ts>=position['deadline'] or due:
                reason='total_loss_stop' if total_stop else position['exit_reason'] if due else 'two_minute_time_exit'
                sell(ts,bar['open'],reason)
        if pending and ts>=pending['fill_at']:
            if not daily_stop and not total_stop:
                buy(ts,pending)
                counts['filled_buys']+=1
            else:
                counts['pending_cancelled_by_risk']+=1
            pending=None

        row=predictions.get(ts)
        p=row['prediction'] if row else None
        if p is not None and not math.isfinite(p):
            p=None
        reason='missing_or_invalid_prediction' if p is None else None
        action='HOLD'
        if position:
            if p is None or p<=0:
                scheduled=min(ts+delay,position['deadline'])
                if position['exit_at'] is None or scheduled<position['exit_at']:
                    position['exit_at']=scheduled
                    position['exit_reason']='model_invalid' if p is None else 'model_nonpositive'
                    action='SELL'
                    if delay==0:
                        sell(ts,bar['open'],position['exit_reason'])
                reason='exit_scheduled'
            else:
                reason='position_open'
        elif pending:
            reason='entry_pending'
        elif p is not None:
            if daily_stop or total_stop:
                reason='risk_stop'
            elif ts+delay+hold>=end:
                reason='window_end_too_close'
            elif p<=hurdle:
                reason='below_execution_cost_and_buffer'
            else:
                pending={'decision_at':ts,'fill_at':ts+delay,'prediction':p}
                action='BUY';reason='positive_edge_after_cost_buffer'
                counts['scheduled_buys']+=1
                if delay==0:
                    buy(ts,pending);pending=None;counts['filled_buys']+=1
        counts[reason]+=1
        if trace:
            decisions.append({'timestamp':ts,'action':action,'reason':reason,
                              'prediction_log_return':p,'required_log_return':hurdle,
                              'feature_ref':row['feature_ref'] if row else None})
        marked=cash+qty*bar['close']*(1-half)*(1-fee)
        peak=max(peak,marked);dd=max(dd,1-marked/peak)
        daily_stop |= marked<=day_start*.98
        total_stop |= marked<=INITIAL*.95
        if total_stop and position:
            # A close is only known when this minute ends; stop at next open.
            position['exit_at']=ts+60;position['exit_reason']='total_loss_stop'
        if trace:
            curve.append({'timestamp':ts,'equity_tmn':marked})
    if position:
        sell(end,prices[end-60]['close'],'terminal_liquidation')
    peak=max(peak,cash);dd=max(dd,1-cash/peak)
    return {'net_return_pct':100*(cash/INITIAL-1),'net_pnl_tmn':cash-INITIAL,
            'final_equity_tmn':cash,'max_drawdown_pct':100*dd,'trades':len(trades),
            'wins':sum(t['pnl_tmn']>0 for t in trades),'fees_tmn':fees,
            'total_stop_triggered':total_stop,'fee_each_side':fee,'full_spread_bps':half*20000,
            'execution_delay_seconds':delay,'max_hold_seconds_after_fill':hold,
            'entry_buffer_bps':buffer*10000,'counts':dict(counts),'trade_log':trades,
            **({'decision_log':decisions,'equity_curve':curve} if trace else {})}


def benchmark(prices,fee,half,delay=DELAY):
    entry=TEST+delay
    q=INITIAL/(1+fee)/(prices[entry]['open']*(1+half))
    final=q*prices[END-60]['close']*(1-half)*(1-fee)
    return {'net_return_pct':100*(final/INITIAL-1),'net_pnl_tmn':final-INITIAL,
            'entry_ts':entry,'exit_ts':END}


def delayed_forecast_audit(predictions,prices,delay=DELAY,hold=HOLD):
    ps=[];ys=[]
    for t,row in predictions.items():
        entry=t+delay;exit_=entry+hold
        if entry in prices and exit_ in prices and exit_<END:
            ps.append(row['prediction'])
            ys.append(math.log(prices[exit_]['open']/prices[entry]['open']))
    p=np.array(ps);y=np.array(ys)
    mse=float(np.mean((p-y)**2));zero=float(np.mean(y*y))
    return {'n':len(ps),'r2_vs_zero':1-mse/zero,'rmse_bps':math.sqrt(mse)*10000,
            'zero_forecast_rmse_bps':math.sqrt(zero)*10000,
            'description':'Frozen FX-adjusted decision-time 2m forecast compared to actual TMN return over 2m after delayed fill; different target, no recalibration'}


def main():
    mp=Path('results/remaining-move-models.json')
    wp=Path('results/intraday-btc-pump-1m-data.json');fp=Path('results/leadlag-wallex-usdt-1m.json')
    artifact=json.loads(mp.read_text());raw=json.loads(wp.read_text())['symbols'];fx=json.loads(fp.read_text())['rows']
    output={'kind':'frozen_two_minute_model_minute_cadence_historical_simulation',
            'start_utc':utc(TEST),'end_utc':utc(END),'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (mp,wp,fp)},
            'features':FEATURES,'model_refit':False,
            'policy':{'decision_frequency_seconds':60,'allocation':'all available cash; independent coin accounts',
                      'execution_delay_seconds':DELAY,'maximum_hold_after_fill_seconds':HOLD,
                      'entry_buffer_bps':BUFFER*10000,'daily_new_buy_pause_pct':2,'initial_capital_loss_stop_pct':5,
                      'sell':'nonpositive/invalid model schedules sell; mandatory time exit; total loss stop',
                      'prediction_target':'FX-adjusted move from decision time to decision+2m; FX forecast assumed zero',
                      'execution_target':'actual TMN move from decision+1m to decision+3m'},
            'limitations':['Previously explored historical evaluation window; no independent new-data validation',
                           'Forecast target and delayed-execution target differ; delayed audit is reported',
                           'Historical bid/ask, depth, queue and actual publication delay unavailable',
                           'Fee scenarios are assumptions, not verified credit-account fees; no credit daily fee',
                           'No exchange orders or production/live config changes'], 'results':{}}
    for symbol in ('BTC','PUMP'):
        rows,prices,hashes=dataset(symbol,raw[symbol+'TMN'],fx)
        model=artifact['models'][symbol]['2']
        assert model['max_label_at']<TEST
        predictions={r['available_at']:{'prediction':predict(model,r),'feature_ref':r['candle_ts']}
                     for r in rows if TEST<=r['available_at']<END}
        result={'binance_hashes':hashes,'model_last_label_at':model['max_label_at'],
                'feature_snapshots':{str(r['candle_ts']):dict(zip(FEATURES,r['x'])) for r in rows if TEST<=r['available_at']<END},
                'delayed_forecast_audit':delayed_forecast_audit(predictions,prices),
                'max_positive_prediction_pct':100*math.expm1(max(r['prediction'] for r in predictions.values())),
                'scenarios':{}}
        for name,fee,half in SCENARIOS:
            sim=simulate(predictions,prices,fee,half,trace=name==SCENARIOS[0][0])
            sim['buy_hold']=benchmark(prices,fee,half)
            result['scenarios'][name]=sim
            print(symbol,name,'return',round(sim['net_return_pct'],4),'trades',sim['trades'],'drawdown',round(sim['max_drawdown_pct'],4))
        result['zero_latency_zero_cost_optimistic_diagnostic']=simulate(predictions,prices,0,0,delay=0)
        result['deployment_status']='not_validated_for_live_execution'
        output['results'][symbol]=result
    Path('results/two-minute-execution-btc-pump.json').write_text(json.dumps(output,indent=2)+'\n')


if __name__=='__main__':
    main()
