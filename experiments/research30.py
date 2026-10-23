"""Auditable 30-day multi-family research, never a live order sender.

Historical books are absent. Historical selection uses the observable 40/30/10
components normalized by 80, explicitly distinct from the requested full screen.
Every scenario is retained. Positive discovery on this examined month is in-sample.
"""
from __future__ import annotations
import csv
import hashlib
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from wallex_agent.monthly_policy import relative_score
from research30_data import ROOT, START, END, TRAIN, WARMUP

OUT=Path('results/research30')
HOUR=3600
INITIAL=5_000_000.
TEHRAN=ZoneInfo('Asia/Tehran')


def dump(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def percentile(values):
    a=np.asarray(values,dtype=float)
    if len(a)==1:return np.array([50.])
    return np.array([100*(sum(a<x)+(sum(a==x)-1)/2)/(len(a)-1) for x in a])


def valid(b,positive_volume=False):
    return bool(b and all(math.isfinite(b[k]) for k in 'ohlcv') and b['l']>0 and
                b['h']>=max(b['o'],b['c'],b['l']) and b['l']<=min(b['o'],b['c']) and
                b['v']>=(1e-20 if positive_volume else 0))


def load_data():
    meta=json.loads((ROOT/'universe.json').read_text())
    symbols=[m['symbol'] for m in meta['markets']]
    bars={};daily={};audit={};N=(END-WARMUP)//HOUR
    for s in symbols:
        raw={int(t):b for t,b in json.loads((ROOT/f'{s}-hourly.json').read_text()).items()}
        ds={int(t):b for t,b in json.loads((ROOT/f'{s}-daily.json').read_text()).items()}
        a=np.full((N,5),np.nan);real=np.zeros(N,dtype=bool)
        for t,b in raw.items():
            if t%HOUR or not WARMUP<=t<END or not valid(b):continue
            i=(t-WARMUP)//HOUR;a[i]=[b[k] for k in 'ohlcv'];real[i]=b['v']>0
        zero_fills=0;bad_days=[];good_days=[]
        # No invented trading: missing intervals are zero-volume only when the
        # captured daily total reconciles with all captured hourly volumes.
        for t in range(WARMUP,END,86400):
            i=(t-WARMUP)//HOUR;b=ds.get(t)
            total=float(np.nansum(a[i:i+24,4]))
            reconciled=valid(b) and math.isclose(total,b['v'],rel_tol=1e-6,abs_tol=1e-6)
            if not reconciled:bad_days.append(t);continue
            good_days.append(t)
            for j in range(i,i+24):
                if not np.isfinite(a[j,3]) and j and np.isfinite(a[j-1,3]):
                    price=a[j-1,3];a[j]=[price,price,price,price,0.];zero_fills+=1
        bars[s]={'a':a,'real':real}
        daily[s]=ds
        audit[s]={'real_hour_bars':int(real.sum()),'verified_zero_volume_fills':zero_fills,
                  'unknown_hours':int((~np.isfinite(a[:,3])).sum()),
                  'volume_reconciled_days':len(good_days),'unreconciled_day_timestamps':bad_days,
                  'listed_at':next(m['created_at'] for m in meta['markets'] if m['symbol']==s)}
    return meta,bars,daily,audit


def build_screen(meta,bars,daily):
    screens={};available=meta['markets']
    for t in range(TRAIN,END,86400):
        rows=[]
        for m in available:
            s=m['symbol'];ds=daily[s]
            if datetime.fromisoformat(m['created_at'].replace('Z','+00:00')).timestamp()>t-7*86400:continue
            past=[ds.get(t-j*86400) for j in range(7,0,-1)]
            if not all(valid(b,True) for b in past):continue
            ranges=[100*(b['h']/b['l']-1) for b in past]
            prev=ds.get(t-4*86400)
            if not valid(prev):continue
            # Bounds on traded TMN turnover, not an exact quote-volume claim.
            last=past[-1];lo=last['l']*last['v'];hi=last['h']*last['v']
            idx=(t-WARMUP)//HOUR
            hours=bars[s]['a'][idx-24:idx]
            approx=float(np.nansum(((hours[:,1]+hours[:,2]+hours[:,3])/3)*hours[:,4]))
            if not lo*(1-1e-6)<=approx<=hi*(1+1e-6):
                continue
            if int(bars[s]['real'][idx-24:idx].sum())<18:continue
            # Avoid a fictitious price from an inactive market; no top-of-book history exists.
            if lo<50_000_000:continue
            rows.append({'symbol':s,'mean7':float(np.mean(ranges)), 'mean3':float(np.mean(ranges[-3:])),
                         'median7':float(np.median(ranges)),'valid_days':7,
                         'return3':last['c']/prev['c']-1,'quote_volume_lower':lo,
                         'quote_volume_upper':hi,'historical_volume_rank_basis':'proven lower bound low*base_volume; exact quote turnover unavailable',
                         'volatility_increasing':bool(np.mean(ranges[-3:])>np.mean(ranges))})
        if rows:
            p7=percentile([r['mean7'] for r in rows]);p3=percentile([r['mean3'] for r in rows]);pv=percentile([r['quote_volume_lower'] for r in rows])
            for j,r in enumerate(rows):
                r['known_score_0_to_80']=float(.4*p7[j]+.3*p3[j]+.1*pv[j])
                r['historical_proxy_score']=r['known_score_0_to_80']/.8
                r['full_score_possible_interval']=[r['known_score_0_to_80'],r['known_score_0_to_80']+20]
            rows.sort(key=lambda r:(-r['historical_proxy_score'],r['symbol']))
            for rank,r in enumerate(rows,1):r['rank']=rank
        screens[t]=rows
    return screens


def features(bars):
    result={}
    for s,z in bars.items():
        a=z['a'];N=len(a);o,h,l,c,v=a.T
        f={k:np.full(N,np.nan) for k in ['atr','ema24','ema72','vwap24','er24','rv24','rv72','volratio','compress6','compress12']}
        for k in (1,3,6,12,24,72):
            ar=np.full(N,np.nan);ar[k:]=c[k:]/c[:-k]-1;f[f'r{k}']=ar
        for k in (6,12,24):
            f[f'upper{k}']=np.full(N,np.nan)
        tr=np.full(N,np.nan)
        for i in range(N):
            if not np.isfinite(c[i]):continue
            tr[i]=h[i]-l[i] if i==0 or not np.isfinite(c[i-1]) else max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
            for k in (24,72):
                prev=f[f'ema{k}'][i-1] if i else np.nan
                f[f'ema{k}'][i]=c[i] if not np.isfinite(prev) else 2/(k+1)*c[i]+(1-2/(k+1))*prev
            if i>=19 and np.all(np.isfinite(tr[i-19:i+1])):
                f['atr'][i]=(19*f['atr'][i-1]+tr[i])/20 if i and np.isfinite(f['atr'][i-1]) else float(np.mean(tr[i-19:i+1]))
            if i>=72 and np.all(np.isfinite(c[i-72:i+1])):
                rets=np.diff(np.log(c[i-72:i+1]));rv24=float(np.std(rets[-24:]));f['rv24'][i]=rv24;f['rv72'][i]=float(np.std(rets))
                delta=np.diff(c[i-24:i+1]);f['er24'][i]=abs(c[i]-c[i-24])/sum(abs(delta)) if sum(abs(delta)) else 0.
                vv=v[i-23:i+1];typ=(h[i-23:i+1]+l[i-23:i+1]+c[i-23:i+1])/3
                f['vwap24'][i]=float(sum(typ*vv)/sum(vv)) if sum(vv)>0 else np.nan
                meanv=float(np.mean(v[i-24:i]));f['volratio'][i]=v[i]/meanv if meanv>0 else 0.
                # Compression is measured BEFORE the signal candle's expansion.
                prior_returns=np.diff(np.log(c[i-25:i]))
                for k in (6,12):f[f'compress{k}'][i]=float(np.std(prior_returns[-k:])/np.std(prior_returns)) if np.std(prior_returns)>0 else np.nan
                for k in (6,12,24):f[f'upper{k}'][i]=float(max(h[i-k:i]))
        result[s]=f
    return result


def grid():
    out=[]
    def add(family,**kw):
        out.append({'id':f'{family}-{sum(x["family"]==family for x in out)+1:03d}',
                    'family':family,'hold':24,'stop_kind':'fixed','stop':.05,'tp':0.,**kw})
    for positive,hold in itertools.product((False,True),(24,48)):
        add('volatility_control',positive=positive,hold=hold)
    for score,regime,hold,profile in itertools.product(('r24','r72','blend'),('none','btc','breadth'),(12,24,48),('fixed','atr','atr_target')):
        add('relative_strength',score=score,regime=regime,hold=hold,
            stop_kind='fixed' if profile=='fixed' else 'atr',stop=.05 if profile=='fixed' else 2.,
            tp=.05 if profile=='atr_target' else 0.)
    for z,er,hold,mult in itertools.product((1.,1.5,2.),(.2,.35),(6,12,24),(1.5,2.5)):
        add('range_reversion',z=z,er=er,hold=hold,stop_kind='atr',stop=mult,target_vwap=True)
    for lookback,compression,vr,hold,profile in itertools.product((6,12,24),(.5,.75),(1.,1.5),(12,24,48),('fixed_target','atr','atr_target')):
        add('compression_breakout',lookback=lookback,compression=compression,volume_ratio=vr,hold=hold,
            stop_kind='fixed' if profile=='fixed_target' else 'atr',
            stop=.03 if profile=='fixed_target' else (3. if profile=='atr_target' else 2.),
            tp=.05 if profile=='fixed_target' else (.1 if profile=='atr_target' else 0.))
    # Entries are scheduled every 48h, allowing identical entry cohorts across exits.
    for exit_kind,tp,hold in itertools.product(('fixed','atr2','atr3','chandelier3'),(0.,.05,.1),(24,48)):
        add('exit_comparison',exit_kind=exit_kind,stop_kind='fixed' if exit_kind=='fixed' else 'atr',
            stop=.05 if exit_kind=='fixed' else (2. if exit_kind=='atr2' else 3.),
            trailing=exit_kind=='chandelier3',tp=tp,hold=hold)
    for horizon,ridge,margin,regime in itertools.product((6,12,24),(10.,100.),(0.,.003,.01),('none','btc')):
        add('ai_filter',horizon=horizon,ridge=ridge,margin=margin,regime=regime,hold=horizon,stop_kind='atr',stop=2.)
    # User's finite 3-5% objective motivates a separate, explicit account policy:
    # liquidate when NET account equity reaches the goal and stay cash to period end.
    # This amendment is registered after the unsuccessful first-stage exploration.
    original=list(out)
    for cfg in original:
        for goal in (.03,.05):
            out.append({**cfg,'id':cfg['id']+f'-goal{int(goal*100)}','profit_goal':goal})
    return out


class Research:
    def __init__(self):
        self.meta,self.bars,self.daily,self.audit=load_data()
        self.screens=build_screen(self.meta,self.bars,self.daily)
        self.f=features(self.bars)
        self.symbols=[m['symbol'] for m in self.meta['markets']]
        self.prec={m['symbol']:int(m['amount_precision']) for m in self.meta['markets']}
        self.top={t:[r['symbol'] for r in rr[:5] if r['symbol'] not in ('USDTTMN','USDCTMN')] for t,rr in self.screens.items()}
        self.ai={};self.model_info={}

    def regime(self,kind,p):
        if kind=='none':return True
        btc=self.f['BTCTMN'];fx=self.f['USDTTMN']
        if kind=='btc':return bool(btc['r24'][p]>fx['r24'][p] and self.bars['BTCTMN']['a'][p,3]>btc['ema72'][p])
        vals=[self.f[s]['r24'][p]-fx['r24'][p] for s in self.symbols if np.isfinite(self.f[s]['r24'][p])]
        return bool(vals and sum(v>0 for v in vals)/len(vals)>.55)

    def choose(self,cfg,i):
        t=WARMUP+i*HOUR;p=i-1;day=t//86400*86400
        candidates=self.top.get(day,[])
        scored=[]
        if cfg['family']=='exit_comparison' and (t-START)%(48*HOUR)!=0:return None
        if not self.regime(cfg.get('regime','none'),p):return None
        for s in candidates:
            a=self.bars[s]['a'];f=self.f[s]
            if not np.isfinite(f['atr'][p]) or not np.isfinite(f['r72'][p]):continue
            if not cfg.get('rank_without_entry_volume') and not self.bars[s]['real'][i]:continue
            score=None
            fam=cfg['family'];c=a[p,3];atr=f['atr'][p]
            if fam in ('volatility_control','exit_comparison'):
                row=next(r for r in self.screens[day] if r['symbol']==s)
                if (fam=='exit_comparison' or cfg.get('positive')) and row['return3']<=0:continue
                score=row['historical_proxy_score']
            elif fam=='relative_strength':
                fx=self.f['USDTTMN'];btc=self.f['BTCTMN']
                at=lambda z:{k:z[k][p] for k in ('r24','r72','rv24','rv72')}
                score=relative_score(s,at(f),at(fx),at(btc),cfg['score'])
                if score is None:continue
            elif fam=='range_reversion':
                z=(f['vwap24'][p]-c)/atr
                if f['er24'][p]>cfg['er'] or z<cfg['z'] or not c>a[p-1,3]:continue
                score=z
            elif fam=='compression_breakout':
                k=cfg['lookback'];compression=f['compress6' if k==6 else 'compress12'][p]
                if not (compression<cfg['compression'] and c>f[f'upper{k}'][p] and f['volratio'][p]>=cfg['volume_ratio']):continue
                score=(c/f[f'upper{k}'][p]-1)/max(atr/c,1e-9)
            elif fam=='ai_filter':
                key=(cfg['horizon'],cfg['ridge']);pred=self.ai.get(key,{}).get((s,i))
                if pred is None or pred<=cfg['margin']:continue
                score=pred
            if score is not None and math.isfinite(score):scored.append((float(score),s))
        if scored and cfg.get('random_choice'):
            return scored[int(self.random_rng.integers(len(scored)))][1]
        return max(scored)[1] if scored else None

    def simulate(self,cfg,start=START,end=END,fee=.003,spread_bps=20,path='OLHC',guards=True,trace=False,micro=None,requirements=None):
        first,last=(start-WARMUP)//HOUR,(end-WARMUP)//HOUR
        if cfg.get('random_choice'):self.random_rng=np.random.default_rng(cfg['_random_seed'])
        half=spread_bps/20_000
        cash=INITIAL;pos=None;peak=INITIAL;dd=0.;fees=0.;trades=[];curve=[];day=None;day_start=INITIAL;daily_stop=False;total_stop=False;blocked=0;goal_hit=False
        goal=cfg.get('profit_goal',0.)
        reset_hours=cfg.get('goal_reset_hours',0)
        profit_reference=INITIAL;loss_reference=INITIAL
        disable_portfolio_stop=cfg.get('portfolio_loss_reset')=='off'
        blocks=[];block_start=start;block_equity=INITIAL;block_trade_start=0;block_fees_start=0.

        def block_report(end_ts,value):
            return {'start_ts':block_start,'end_ts':end_ts,'start_equity_tmn':block_equity,
                    'end_equity_tmn':value,'return_pct':(value/block_equity-1)*100,
                    'pnl_tmn':value-block_equity,'trades_closed':len(trades)-block_trade_start,
                    'fees_tmn':fees-block_fees_start,'profit_goal_hit':bool(goal_hit),
                    'portfolio_stop':bool(total_stop),'loss_reference_tmn':loss_reference}

        def equity(price=None):
            return cash if pos is None else cash+pos['qty']*(pos['last'] if price is None else price)*(1-half)*(1-fee)

        def close(raw,i,reason,event_ts=None):
            nonlocal cash,pos,fees,goal_hit
            fill=raw*(1-half);proceeds=pos['qty']*fill;f=proceeds*fee;fees+=f;cash+=proceeds-f
            exit_ts=min(end,event_ts if event_ts is not None else WARMUP+i*HOUR)
            trades.append({'symbol':pos['s'],'entry_ts':pos.get('actual_entry_ts',WARMUP+pos['i']*HOUR),'exit_ts':exit_ts,
                           'entry_fill':pos['fill'],'exit_fill':fill,'allocated_tmn':pos['basis'],
                           'qty':pos['qty'],'buy_fee_tmn':pos['buy_fee'],'sell_fee_tmn':f,
                           'pnl_tmn':cash-pos['basis'],'return_pct':(cash/pos['basis']-1)*100,
                           'hours_held':(exit_ts-pos.get('actual_entry_ts',WARMUP+pos['i']*HOUR))/HOUR,'reason':reason})
            pos=None
            if goal and cash>=profit_reference*(1+goal)-1e-6:goal_hit=True

        for i in range(first,last):
            t=WARMUP+i*HOUR;localday=datetime.fromtimestamp(t,TEHRAN).date();exited=False
            if pos is not None:
                b=self.bars[pos['s']]['a'][i]
                if np.isfinite(b[0]):pos['last']=float(b[0])
                if micro is not None:
                    key=(pos['s'],i)
                    if requirements is not None:requirements.add(key)
                    if micro.get(key):pos['last']=micro[key][0]['o']
            marked=equity()
            if reset_hours and i>first and (i-first)%reset_hours==0:
                blocks.append(block_report(t,marked))
                block_start=t;block_equity=marked;block_trade_start=len(trades);block_fees_start=fees
                profit_reference=marked;goal_hit=False
                if cfg.get('portfolio_loss_reset')=='block':loss_reference=marked;total_stop=False
                if disable_portfolio_stop:total_stop=False
                # Carry an open position across the boundary; only account goal
                # reference changes. Its original price stop and age are preserved.
                if pos is not None and goal:
                    goal_price=(profit_reference*(1+goal)-cash)/(pos['qty']*(1-half)*(1-fee))
                    own_target=pos['fill']*(1+cfg['tp']) if cfg['tp'] else 0.
                    pos['target']=min(own_target,goal_price) if own_target else goal_price
                    pos['target_reason']='net_account_profit_goal' if not own_target or goal_price<=own_target else 'target_intrahour'
            if localday!=day:day=localday;day_start=marked;daily_stop=False
            daily_stop|=guards and marked<=day_start*.98
            total_stop|=guards and not disable_portfolio_stop and marked<=loss_reference*.95
            if pos is not None and self.bars[pos['s']]['real'][i]:
                b=self.bars[pos['s']]['a'][i];o=pos['last']
                if goal and marked>=profit_reference*(1+goal):close(o,i,'net_account_profit_goal');exited=True
                elif total_stop:close(o,i,'portfolio_stop_next_open');exited=True
                elif o<=pos['stop']:close(o,i,'stop_gap');exited=True
                elif pos['target'] and o>=pos['target']:close(o,i,'target_gap');exited=True
                elif i-pos['i']>=cfg['hold']:close(o,i,'time_exit');exited=True
            if pos is None and not exited:
                if daily_stop or total_stop or goal_hit:blocked+=1
                else:
                    s=self.choose(cfg,i)
                    # Fill/no-fill of the first choice is checked only AFTER
                    # ranking. No runner-up is substituted using future volume.
                    if s and not self.bars[s]['real'][i]:s=None
                    if s:
                        a=self.bars[s]['a'];raw=float(a[i,0]);atr=float(self.f[s]['atr'][i-1]);fill=raw*(1+half)
                        actual_entry_ts=t
                        if micro is not None:
                            key=(s,i)
                            if requirements is not None:requirements.add(key)
                            if micro.get(key):
                                raw=micro[key][0]['o'];actual_entry_ts=micro[key][0]['ts'];fill=raw*(1+half)
                        distance=fill*cfg['stop'] if cfg['stop_kind']=='fixed' else cfg['stop']*atr
                        target=fill*(1+cfg['tp']) if cfg['tp'] else 0.
                        if cfg.get('target_vwap'):target=float(self.f[s]['vwap24'][i-1])
                        hurdle=(1+fee)/(1-fee)/(1-half)
                        if cfg.get('target_vwap') and target<=fill*hurdle:continue
                        scale=10**self.prec[s];qty=math.floor(cash/(1+fee)/fill*scale)/scale
                        if qty<=0:continue
                        basis=cash;buy_fee=qty*fill*fee;cash-=qty*fill+buy_fee;fees+=buy_fee
                        pos={'s':s,'qty':qty,'i':i,'fill':fill,'basis':basis,'buy_fee':buy_fee,
                             'stop':max(0.,fill-distance),'distance':distance,'target':target,
                             'last':raw,'peak':raw,'actual_entry_ts':actual_entry_ts}
                        if goal:
                            goal_price=(profit_reference*(1+goal)-cash)/(qty*(1-half)*(1-fee))
                            if not pos['target'] or goal_price<pos['target']:
                                pos['target']=goal_price;pos['target_reason']='net_account_profit_goal'
            if pos is not None:
                held_s=pos['s'];a=self.bars[held_s]['a'][i]
                if micro is not None and requirements is not None:requirements.add((held_s,i))
                minute_rows=micro.get((held_s,i)) if micro is not None else None
                pieces=minute_rows if minute_rows else [{'ts':t,**{k:float(a[j]) for j,k in enumerate('ohlcv')}}]
                for piece in pieces:
                  if piece['v']>0 and np.isfinite(piece['c']):
                    # A gap at any minute is filled at the first observed price,
                    # never at a stop/target price skipped by that gap.
                    if piece['o']<=pos['stop']:
                        close(piece['o'],i,'stop_gap',piece['ts']);break
                    if pos['target'] and piece['o']>=pos['target']:
                        close(piece['o'],i,pos.get('target_reason','target_gap'),piece['ts']);break
                    # Two explicit OHLC paths expose the unresolved intrahour order.
                    points=[piece[k] for k in ('olhc' if path=='OLHC' else 'ohlc')]
                    prev=points[0]
                    for price in points[1:]:
                        if price<prev and price<=pos['stop']:
                            close(min(prev,pos['stop']),i,'stop_intraminute' if minute_rows else 'stop_intrahour',piece['ts']);break
                        if price>prev and pos['target'] and price>=pos['target']:
                            close(max(prev,pos['target']),i,pos.get('target_reason','target_intraminute' if minute_rows else 'target_intrahour'),piece['ts']);break
                        if cfg.get('trailing') and price>pos['peak']:
                            pos['peak']=price
                            # Completed previous hour's ATR only; no future volatility.
                            mult=cfg['stop'];atr=float(self.f[pos['s']]['atr'][i-1])
                            pos['stop']=max(pos['stop'],price-mult*atr)
                        prev=price
                    if pos is not None:pos['last']=piece['c']
                    else:break
            eq=equity();peak=max(peak,eq);dd=max(dd,1-eq/peak)
            daily_stop|=guards and eq<=day_start*.98
            total_stop|=guards and not disable_portfolio_stop and eq<=loss_reference*.95
            if trace:curve.append({'ts':t+HOUR,'equity_tmn':eq})
        if pos is not None:close(pos['last'],last,'end_of_test_mark_liquidation')
        if reset_hours:blocks.append(block_report(end,cash))
        returns=[t['return_pct'] for t in trades]
        wins=[t['pnl_tmn'] for t in trades if t['pnl_tmn']>0];losses=[t['pnl_tmn'] for t in trades if t['pnl_tmn']<0]
        return {'id':cfg['id'],'family':cfg['family'],'return_pct':(cash/INITIAL-1)*100,
                'final_tmn':cash,'pnl_tmn':cash-INITIAL,'max_drawdown_pct':dd*100,
                'trades':len(trades),'wins':len(wins),'win_rate_pct':100*len(wins)/len(trades) if trades else 0.,
                'profit_factor':sum(wins)/abs(sum(losses)) if losses else None,
                'fees_tmn':fees,'median_hold_hours':float(np.median([t['hours_held'] for t in trades])) if trades else 0.,
                'portfolio_stop':bool(total_stop),'blocked_new_buy_hours':blocked,
                'profit_goal_hit':bool(goal_hit),
                'monthly_blocks':blocks,
                'mean_trade_return_pct':float(np.mean(returns)) if returns else 0.,
                'trade_log':trades if trace else [],'curve':curve}

    def ml_features(self,s,i):
        p=i-1;f=self.f[s];a=self.bars[s]['a'];btc=self.f['BTCTMN'];fx=self.f['USDTTMN'];c=a[p,3]
        day=(WARMUP+i*HOUR)//86400*86400
        rows=self.screens.get(day,[]);row=next((r for r in rows if r['symbol']==s),None)
        if row is None:return None
        x=[f['r1'][p],f['r6'][p],f['r24'][p],f['r72'][p],f['r24'][p]-btc['r24'][p],
           fx['r24'][p],btc['r24'][p]-fx['r24'][p],f['atr'][p]/c,f['rv24'][p],
           f['er24'][p],(c-f['vwap24'][p])/f['atr'][p],min(10.,f['volratio'][p]),
           row['mean7']/100,row['mean3']/100,row['historical_proxy_score']/100]
        return x if all(math.isfinite(q) for q in x) else None

    def train_ai(self):
        feature_names=['r1','r6','r24','r72','relative_btc24','fx24','btc_fx24','atr_pct','rv24','efficiency24','vwap_z','volume_ratio','daily_range7','daily_range3','screen_score']
        cost=-math.log((1-.003)/(1+.003)*(1-.001)/(1+.001))
        first=(TRAIN-WARMUP)//HOUR;split=(START-WARMUP)//HOUR;last=(END-WARMUP)//HOUR
        valid_start=split-7*24
        examples=[]
        for i in range(first,last):
            day=(WARMUP+i*HOUR)//86400*86400
            for s in self.top.get(day,[]):
                if not self.bars[s]['real'][i]:continue
                x=self.ml_features(s,i)
                if x is not None:examples.append((s,i,x))
        for horizon,ridge in itertools.product((6,12,24),(10.,100.)):
            eligible=[]
            for s,i,x in examples:
                a=self.bars[s]['a'];j=i+horizon
                if j>=len(a) or not self.bars[s]['real'][j]:continue
                y=math.log(a[j,0]/a[i,0])-cost
                if i<split and j<split:eligible.append((s,i,x,y,j))
            tr=[z for z in eligible if z[4]<valid_start]
            va=[z for z in eligible if z[1]>=valid_start]
            def fit(rows):
                X=np.array([z[2] for z in rows]);y=np.array([z[3] for z in rows]);mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-8,1.,sd);ym=float(y.mean());Z=(X-mu)/sd
                # einsum avoids platform BLAS floating-status warnings; inputs and
                # learned coefficients must remain finite, never silently imputed.
                gram=np.einsum('ni,nj->ij',Z,Z)
                rhs=np.einsum('ni,n->i',Z,y-ym)
                coef=np.linalg.solve(gram+ridge*np.eye(Z.shape[1]),rhs)
                if not np.isfinite(coef).all():raise ValueError('nonfinite ridge coefficients')
                return mu,sd,ym,coef
            if len(tr)<100 or len(va)<30:continue
            mu,sd,ym,coef=fit(tr)
            vx=np.array([z[2] for z in va]);vy=np.array([z[3] for z in va]);vp=np.einsum('ni,i->n',(vx-mu)/sd,coef)+ym
            mse=float(np.mean((vp-vy)**2));zero=float(np.mean(vy**2));passed=mse<zero
            info={'horizon':horizon,'ridge':ridge,'features':feature_names,'training_rows':len(tr),'validation_rows':len(va),
                  'validation_mse':mse,'zero_mse':zero,'validation_pass':bool(passed),
                  'training_labels_end_before':WARMUP+valid_start*HOUR,'validation_labels_end_before':START}
            # Gate is defined before 30-day evaluation. Invalid models remain HOLD.
            if passed:
                mu,sd,ym,coef=fit(eligible)
                info.update(mean=mu.tolist(),scale=sd.tolist(),intercept=ym,coefficients=coef.tolist(),refit_rows=len(eligible))
                preds={}
                for s,i,x in examples:
                    if i>=split:preds[s,i]=float(np.sum((np.array(x)-mu)/sd*coef)+ym)
                self.ai[horizon,ridge]=preds
            self.model_info[f'{horizon}-{ridge}']=info
        dump(OUT/'ai-models.json',self.model_info)
        print('AI models validation passed',sum(m['validation_pass'] for m in self.model_info.values()),'/',len(self.model_info),flush=True)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    configs=grid()
    dump(OUT/'preregistered-grid.json',{'created_at_utc':datetime.now(timezone.utc).isoformat(),
           'target':'3-5% net exploratory return, not a guaranteed objective',
           'period':[START,END],'prior_period':[TRAIN,START],'scenarios':configs,
           'historical_screen':'40/30/10 known components divided by .8; missing cost component explicitly unavailable',
           'guards':'2% Tehran-day pause new buys, 5% initial-equity loss halts account; hourly checks',
           'costs':'observed 0.3% taker/side + assumed 20bps full spread; no credit fee',
           'selection':'choose one champion per family on preceding 30 days; report all target-month results as discovery'})
    r=Research();dump(OUT/'data-audit.json',r.audit);dump(OUT/'daily-screens.json',r.screens)
    print('features ready',len(r.symbols),'symbols',len(configs),'configs',flush=True)
    r.train_ai()
    rows=[]
    for j,cfg in enumerate(configs,1):
        # Both paths are computed for target month. Previous-month selection is
        # meaningful for deterministic families; AI uses its own prior validation.
        a=r.simulate(cfg,path='OLHC');b=r.simulate(cfg,path='OHLC')
        train=r.simulate(cfg,start=TRAIN,end=START,path='OLHC') if cfg['family']!='ai_filter' else None
        small={k:v for k,v in a.items() if k not in ('trade_log','curve')}
        small.update(config=cfg,other_path_return_pct=b['return_pct'],other_path_max_dd_pct=b['max_drawdown_pct'],
                     conservative_return_pct=min(a['return_pct'],b['return_pct']),
                     previous_month_return_pct=train['return_pct'] if train else None,
                     previous_month_trades=train['trades'] if train else None)
        rows.append(small)
        if j%50==0:print('scenarios',j,'/',len(configs),'positive',sum(z['conservative_return_pct']>0 for z in rows),flush=True)
    dump(OUT/'all-scenarios.json',rows)
    fields=[k for k in rows[0] if k!='config']+['config_json']
    with (OUT/'all-scenarios.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:w.writerow({**{k:v for k,v in row.items() if k!='config'},'config_json':json.dumps(row['config'],sort_keys=True)})
    selected={};discoveries={}
    for family in sorted({x['family'] for x in rows}):
        pool=[x for x in rows if x['family']==family]
        discoveries[family]=max(pool,key=lambda x:x['conservative_return_pct'])
        if family=='ai_filter':
            # Fixed first passing model/lowest threshold; no target-return selection.
            selected[family]=next((x for x in pool if r.model_info.get(f"{x['config']['horizon']}-{x['config']['ridge']}",{}).get('validation_pass')),pool[0])
        else:
            selected[family]=max(pool,key=lambda x:(x['previous_month_return_pct'] if x['previous_month_return_pct'] is not None else -1e10))
    detailed={}
    ids={v['id'] for v in selected.values()}|{v['id'] for v in discoveries.values()}
    for row in rows:
        if row['id'] in ids:
            cfg=row['config'];detail=r.simulate(cfg,trace=True)
            detail['zero_cost_diagnostic']=r.simulate(cfg,fee=0.,spread_bps=0.)
            detail['extra_25bps_each_side']=r.simulate(cfg,spread_bps=70.)
            detail['no_portfolio_guards_diagnostic']=r.simulate(cfg,guards=False)
            detailed[row['id']]=detail
    dump(OUT/'selected-trade-logs.json',detailed)
    dump(OUT/'summary.json',{'period_start_utc':datetime.fromtimestamp(START,timezone.utc).isoformat(),
         'period_end_utc':datetime.fromtimestamp(END,timezone.utc).isoformat(),
         'scenarios':len(rows),'positive_both_paths':sum(x['conservative_return_pct']>0 for x in rows),
         'at_least_3pct_both_paths':sum(x['conservative_return_pct']>=3 for x in rows),
         'selected_on_previous_month':selected,'best_seen_in_target_month_discovery':discoveries,
         'source_manifest_sha256':hashlib.sha256((ROOT/'manifest.json').read_bytes()).hexdigest()} )
    print('DONE',json.dumps({k:{'id':v['id'],'return':v['conservative_return_pct']} for k,v in discoveries.items()}),flush=True)


if __name__=='__main__':main()
