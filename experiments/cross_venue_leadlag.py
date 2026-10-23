"""BTC/PUMP Binance-to-Wallex lead/lag measurement, not a trading backtest.

One-minute log returns, UTC alignment, exact observations only (no forward fill).
Hourly block bootstrap; hourly reference signals use only information available
at the hour boundary. Binance daily archive timestamps are microseconds.
"""
import csv
import hashlib
import io
import json
import math
import random
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

START = 1792465200  # same start as earlier experiments: Oct 20 03:00 UTC
END = 1792627200  # Oct 22 00:00 UTC; latest complete Binance archive day
SYMBOLS = ('BTC', 'PUMP')


def load_binance(symbol):
    rows = {}
    hashes = {}
    for p in sorted(Path('results/binance-spot-1m').glob(f'{symbol}USDT-1m-*.zip')):
        hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        with zipfile.ZipFile(p) as z:
            for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                stamp = int(r[0])
                ts = stamp // (1_000_000 if stamp > 10**14 else 1000)
                if ts in rows:
                    raise ValueError('duplicate Binance timestamp')
                if ts % 60:
                    raise ValueError('unaligned Binance timestamp')
                rows[ts] = {'open': float(r[1]), 'close': float(r[4]), 'volume': float(r[5])}
    for ts in range(START - 3600, END, 60):
        if ts not in rows:
            raise ValueError('missing Binance test minute')
    return rows, hashes


def ret(rows, t, previous=None):
    previous = t - 60 if previous is None else previous
    if t not in rows or previous not in rows:
        return None
    return math.log(rows[t]['close'] / rows[previous]['close'])


def corr(pairs):
    if len(pairs) < 3:
        return None
    n = len(pairs)
    sx, sy = sum(x for x, y in pairs), sum(y for x, y in pairs)
    xx, yy, xy = sum(x*x for x, y in pairs), sum(y*y for x, y in pairs), sum(x*y for x, y in pairs)
    den = (xx-sx*sx/n)*(yy-sy*sy/n)
    return (xy-sx*sy/n)/math.sqrt(den) if den > 0 else None


def quantile(xs, q):
    xs = sorted(xs)
    p = (len(xs)-1)*q
    low = int(p)
    return xs[low] + (xs[min(low+1, len(xs)-1)]-xs[low])*(p-low)


def stats(records):
    # record = (UTC reference minute, x, y); resample whole one-hour groups.
    pairs = [(x, y) for t, x, y in records]
    groups = {}
    for t, x, y in records:
        groups.setdefault((t+60)//3600, []).append((x, y))
    moments = []
    for ps in groups.values():
        moments.append((len(ps), sum(x for x,y in ps), sum(y for x,y in ps),
                        sum(x*x for x,y in ps), sum(y*y for x,y in ps), sum(x*y for x,y in ps)))
    rng = random.Random(20261022)
    bootstrap = []
    for _ in range(1500):
        samples = [rng.choice(moments) for _ in moments]
        n,sx,sy,xx,yy,xy = (sum(s[k] for s in samples) for k in range(6))
        den=(xx-sx*sx/n)*(yy-sy*sy/n)
        if den > 0:
            bootstrap.append((xy-sx*sy/n)/math.sqrt(den))
    nonzero=[(x,y) for x,y in pairs if x != 0 and y != 0]
    return {'n': len(records), 'hour_blocks': len(groups), 'correlation': corr(pairs),
            'hour_block_bootstrap_95ci': [quantile(bootstrap,.025),quantile(bootstrap,.975)],
            'nonzero_pairs': len(nonzero),
            'same_sign_pct_nonzero': 100*sum(x*y>0 for x,y in nonzero)/len(nonzero) if nonzero else None}


def analyze(symbol, wallex, fx):
    binance, hashes = load_binance(symbol)
    w = {r['timestamp']: {'close':float(r['close']), 'open':float(r['open'])} for r in wallex}
    normalized = {t: {'close':r['close']/fx[t]['close']} for t,r in w.items() if t in fx}
    volumes = {r['timestamp']:float(r['volume']) for r in wallex}
    lags = {}
    for k in range(-5, 6):
        adjusted, raw = [], []
        for t in range(START,END,60):
            # Never include a future local minute outside the evaluation window.
            if not START <= t+k*60 < END:
                continue
            x = ret(binance,t)
            y = ret(normalized,t+k*60)
            yr = ret(w,t+k*60)
            if x is not None and y is not None:
                adjusted.append((t,x,y))
            if x is not None and yr is not None:
                raw.append((t,x,yr))
        lags[str(k)] = {'fx_adjusted': stats(adjusted), 'raw_tmn_correlation':corr([(x,y) for t,x,y in raw]),
                        'raw_n':len(raw)}
    horizons = {}
    for k in range(1,6):
        records = []
        for t in range(START,END-k*60,60):
            x,y = ret(binance,t),ret(normalized,t+k*60,t)
            if x is not None and y is not None:
                records.append((t,x,y))
        horizons[str(k)] = stats(records)
    # Only 45 regularly spaced signals: previous completed five Binance minutes
    # at each hour boundary; observe local adjusted returns afterward.
    hourly=[]
    for hour in range(START,END,3600):
        t = hour - 60  # completed candle close known at the hour boundary
        x = ret(binance,t,t-5*60)
        item={'decision_utc':datetime.fromtimestamp(hour,timezone.utc).isoformat(),
              'binance_previous_5m_bps':x*10000, 'local_forward_bps':{}}
        for minutes in (1,2,3,4,5,60):
            if t+minutes*60 >= END:
                continue
            y=ret(normalized,t+minutes*60,t)
            if y is not None:
                item['local_forward_bps'][str(minutes)]=y*10000
        hourly.append(item)
    hourly_summary={}
    for minutes in (1,2,3,4,5,60):
        ps=[(r['binance_previous_5m_bps'],r['local_forward_bps'][str(minutes)])
            for r in hourly if str(minutes) in r['local_forward_bps']]
        nonzero=[(x,y) for x,y in ps if x!=0 and y!=0]
        hourly_summary[str(minutes)]={'n':len(ps),'correlation':corr(ps),
            'mean_signed_future_bps':statistics.mean(y*(1 if x>0 else -1) for x,y in ps if x!=0),
            'same_sign_pct_nonzero':100*sum(x*y>0 for x,y in nonzero)/len(nonzero) if nonzero else None,
            'positive_signal_count':sum(x>0 for x,y in ps),
            'mean_future_bps_when_binance_positive':statistics.mean(y for x,y in ps if x>0),
            'mean_future_bps_unconditional':statistics.mean(y for x,y in ps)}
    robustness={}
    for k in (0,1,2):
        first,second,active=[],[],[]
        for t in range(START,END-k*60,60):
            x,y=ret(binance,t),ret(normalized,t+k*60)
            if x is None or y is None:
                continue
            (first if t<(START+END)//2 else second).append((x,y))
            if volumes.get(t+k*60,0)>0 and volumes.get(t+(k-1)*60,0)>0:
                active.append((x,y))
        robustness[str(k)]={'first_half_correlation':corr(first),'first_half_n':len(first),
                            'second_half_correlation':corr(second),'second_half_n':len(second),
                            'nonzero_local_volume_correlation':corr(active),'nonzero_local_volume_n':len(active)}
    return {'binance_source_hashes':hashes,'lag_definition':'corr(Binance return[t], FX-adjusted Wallex return[t+k]); k>0 means Binance leads',
            'lagged_1m_returns':lags,'forward_cumulative_response':horizons,
            'robustness':robustness,
            'hourly_previous_5m_signal':hourly_summary,'hourly_records':hourly,
            'test_minutes':(END-START)//60,'local_minutes':sum(START<=t<END for t in w),
            'fx_matched_minutes':sum(START<=t<END for t in normalized)}


def main():
    wp=Path('results/intraday-btc-pump-1m-data.json')
    fp=Path('results/leadlag-wallex-usdt-1m.json')
    raw=json.loads(wp.read_text())['symbols']
    fx={r['timestamp']:{'close':float(r['close'])} for r in json.loads(fp.read_text())['rows']}
    result={'kind':'exploratory_cross_venue_leadlag_not_trading_pnl',
            'start_utc':datetime.fromtimestamp(START,timezone.utc).isoformat(),
            'end_utc':datetime.fromtimestamp(END,timezone.utc).isoformat(),
            'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (wp,fp)},
            'method':'1m log returns; Wallex TMN price divided by simultaneous USDTTMN; exact alignment, no forward filling; 1500 one-hour-block bootstrap samples',
            'limitations':['Only 45 hours; 11 tested correlation lags and 5 forward horizons',
                           'Bootstrap intervals are pointwise, not adjusted for multiple comparisons',
                           'Candles are trade prices, not executable quotes; stale/zero-trade prices and bid-ask bounce can affect estimates',
                           'Close is available after its one-minute bar ends; historical publication/network delays unknown',
                           'No historical depth, maker queue, transaction cost or execution simulation',
                           'No causality or profitable strategy established'], 'results':{}}
    for symbol in SYMBOLS:
        result['results'][symbol]=analyze(symbol,raw[symbol+'TMN'],fx)
    out=Path('results/cross-venue-leadlag-btc-pump.json')
    out.write_text(json.dumps(result,indent=2)+'\n')
    for s,r in result['results'].items():
        print(s,'matched',r['fx_matched_minutes'],'/',r['test_minutes'])
        for k in range(0,6):
            a=r['lagged_1m_returns'][str(k)]['fx_adjusted']
            print(k,'corr',round(a['correlation'],4),'CI',[round(v,4) for v in a['hour_block_bootstrap_95ci']],'n',a['n'])
        print('hourly',r['hourly_previous_5m_signal'])


if __name__=='__main__':
    main()
