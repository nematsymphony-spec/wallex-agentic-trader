"""Extend the frozen profitable policy to 60/90 days without parameter search."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import research30 as engine
from wallex_agent.clients import WallexPublic

DATA=Path('data/research90-frozen')
OUT=Path('results/research90-frozen')
END=engine.END
WARMUP=END-98*86400
EARLY_END=engine.WARMUP
TZ=ZoneInfo('Asia/Tehran')


def dump(path,x):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def acquire():
    meta=json.loads(Path('data/research30/universe.json').read_text())
    dump(DATA/'universe.json',meta)
    queries=[]
    for m in meta['markets']:
        for kind,resolution in [('daily','1D'),('hourly','60')]:
            if (DATA/f"{m['symbol']}-{kind}.json").exists():continue
            step=30*86400 if kind=='daily' else 20*86400
            for lo in range(WARMUP,EARLY_END,step):
                queries.append((m['symbol'],kind,resolution,lo,min(EARLY_END,lo+step)))
    def fetch(q):
        s,kind,res,lo,hi=q;p=DATA/'raw'/f'{s}-{kind}-{lo}-{hi}.json'
        if p.exists():return
        for attempt in range(3):
            try:
                raw=WallexPublic().get('/v1/udf/history',{'symbol':s,'resolution':res,'from':lo,'to':hi-1})
                if raw.get('s')not in('ok','no_data'):raise ValueError('bad history status')
                dump(p,{'query':q,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'response':raw});return
            except Exception:
                if attempt==2:raise
                time.sleep(attempt+1)
    print('new history queries',len(queries),flush=True)
    with ThreadPoolExecutor(max_workers=4)as ex:
        futures=[ex.submit(fetch,q)for q in queries]
        for count,f in enumerate(as_completed(futures),1):
            f.result()
            if count%30==0 or count==len(futures):print('history',count,'/',len(futures),flush=True)
    for m in meta['markets']:
        s=m['symbol']
        for kind in ('daily','hourly'):
            target=DATA/f'{s}-{kind}.json'
            if target.exists():continue
            rows={}
            for p in sorted((DATA/'raw').glob(f'{s}-{kind}-*.json')):
                raw=json.loads(p.read_text())['response']
                for i,t in enumerate(raw.get('t',[])):
                    if WARMUP<=t<EARLY_END:rows[str(t)]={k:float(raw[k][i])for k in 'ohlcv'}
            rows.update(json.loads(Path(f'data/research30/{s}-{kind}.json').read_text()))
            dump(target,rows)
    dump(DATA/'manifest.json',{'warmup':WARMUP,'end':END,'seed_data':'data/research30',
         'hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in DATA.glob('*.json')if p.name!='manifest.json'}})


def make_research():
    # A new research instance reads extended data; original 30-day files and
    # frozen strategy parameters are preserved. No grid or fitting is performed.
    engine.ROOT=DATA;engine.WARMUP=WARMUP;engine.TRAIN=END-90*86400
    return engine.Research()


def minute_replay(r,configs):
    micro={};raw_days={};runs={};coverage={};unresolved=[]
    def load():
        for folder in [Path('data/research30/minutes'),DATA/'minutes']:
            for p in folder.glob('*.json'):
                raw=json.loads(p.read_text())
                if 'day'not in raw:continue
                s,day=raw['symbol'],raw['day'];d=raw['response'];raw_days[s,day]=raw
                byhour={}
                for j,t in enumerate(d.get('t',[])):
                    b={'ts':int(t),**{k:float(d[k][j])for k in 'ohlcv'}}
                    if b['v']>0:byhour.setdefault(int(t)//3600*3600,[]).append(b)
                for t in range(day,day+86400,3600):
                    i=(t-WARMUP)//3600
                    if not 0<=i<len(r.bars[s]['a']):continue
                    parts=sorted(byhour.get(t,[]),key=lambda b:b['ts'])
                    volume=sum(b['v']for b in parts)
                    if not r.bars[s]['real'][i]or math.isclose(volume,float(r.bars[s]['a'][i,4]),rel_tol=1e-6,abs_tol=1e-6):micro[s,i]=parts
    for iteration in range(12):
        load();required=set();runs={};coverage={}
        for days in(30,60,90):
            for goal,cfg in configs.items():
                for path in('OLHC','OHLC'):
                    key=f'{days}d-goal{goal}-{path}';req=set()
                    result=r.simulate(cfg,start=END-days*86400,end=END,path=path,trace=True,micro=micro,requirements=req)
                    result['minute_unresolved']=[{'symbol':s,'hour':WARMUP+i*3600}for s,i in sorted(req-set(micro))]
                    result['minute_required_hours']=len(req)
                    runs[key]=result;required|=req
        missing=required-set(micro)
        needed={(s,(WARMUP+i*3600)//86400*86400)for s,i in missing}-set(raw_days)
        print('minute replay',iteration,'hours',len(required),'unresolved',len(missing),'new symbol-days',len(needed),flush=True)
        if not missing:break
        if not needed:
            unresolved=[{'symbol':s,'hour':WARMUP+i*3600}for s,i in sorted(missing)];break
        def fetch(pair):
            s,day=pair
            for attempt in range(3):
                try:
                    raw=WallexPublic().get('/v1/udf/history',{'symbol':s,'resolution':'1','from':day,'to':day+86399})
                    if raw.get('s')not in('ok','no_data'):raise ValueError('bad minute status')
                    dump(DATA/'minutes'/f'{s}-{day}.json',{'symbol':s,'day':day,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'response':raw});return
                except Exception:
                    if attempt==2:raise
        with ThreadPoolExecutor(max_workers=4)as ex:list(ex.map(fetch,sorted(needed)))
    return runs,unresolved


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    original=json.loads(Path('results/research30/summary.json').read_text())['selected_on_previous_month']['relative_strength']['config']
    assert original['score']=='r24'and original['regime']=='none'and original['hold']==48 and original['stop_kind']=='fixed'and original['stop']==.05
    configs={str(goal):{**original,'id':f'frozen-relative-strength-goal{goal}','profit_goal':goal/100}for goal in(3,5)}
    dump(OUT/'frozen-plan.json',{'created_at_utc':datetime.now(timezone.utc).isoformat(),'configs':configs,
         'windows_days':[30,60,90],'end':END,'initial_tmn':5_000_000,
         'profit_goal_reset':'none within each independent period; cash after goal until period end',
         'costs':'0.3% taker each side + assumed full spread 20bps',
         'risk':'same 2% Tehran-day buy pause and 5% initial-equity halt, hourly review',
         'parameter_search':False,'universe':'same frozen currently allowed 62 TMN spot markets',
         'limitations':'same historical screen proxy; no historical depth or exact quote turnover'})
    acquire();r=make_research()
    dump(OUT/'data-audit.json',r.audit);dump(OUT/'daily-screens.json',r.screens)
    runs,unresolved=minute_replay(r,configs)
    # Identical 30-day replay is an integration gate for extending the data window.
    previous=json.loads(Path('results/research30/minute-replay.json').read_text())['runs']
    for goal in(3,5):
        a=runs[f'30d-goal{goal}-OLHC'];b=previous[f'relative_strength-007-goal{goal}-OLHC']
        if abs(a['return_pct']-b['return_pct'])>1e-8 or a['trade_log']!=b['trade_log']:
            # Config IDs do not appear within trade records; any difference here
            # signals data/engine divergence that must be inspected, not hidden.
            raise ValueError('30-day replay changed after extending the warmup')
    benchmarks={};original_choose=r.choose
    for days in(30,60,90):
        start=END-days*86400
        for s in('BTCTMN','PUMPTMN'):
            first=(start-WARMUP)//3600
            while first<len(r.bars[s]['real'])and not r.bars[s]['real'][first]:first+=1
            r.choose=lambda cfg,i:s if i==first else None
            cfg={'id':f'buy_hold_{s}_{days}d','family':'benchmark','hold':10000,'stop_kind':'fixed','stop':.99,'tp':0.}
            benchmarks[f'{days}d-{s}']=r.simulate(cfg,start=start,end=END,guards=False,trace=True)
    r.choose=original_choose
    result={'frozen_configs':configs,'end_utc':datetime.fromtimestamp(END,timezone.utc).isoformat(),
            'runs':runs,'unresolved_minutes':unresolved,'buy_hold_hourly_benchmarks':benchmarks,
            'integration_check_30d_identical':True,'parameter_search_performed':False}
    dump(OUT/'results.json',result)
    rows=[]
    for days in(30,60,90):
        for goal in(3,5):
            a=runs[f'{days}d-goal{goal}-OLHC'];b=runs[f'{days}d-goal{goal}-OHLC']
            rows.append({'days':days,'start_tehran':datetime.fromtimestamp(END-days*86400,TZ).isoformat(),
                         'goal_pct':goal,'return_pct':a['return_pct'],'other_path_return_pct':b['return_pct'],
                         'pnl_tmn':a['pnl_tmn'],'final_tmn':a['final_tmn'],'max_drawdown_hourly_pct':a['max_drawdown_pct'],
                         'trades':a['trades'],'wins':a['wins'],'fees_tmn':a['fees_tmn'],'profit_goal_hit':a['profit_goal_hit'],
                         'portfolio_stop':a['portfolio_stop'],'minute_unresolved_count':len(a['minute_unresolved']),
                         'symbols':','.join(dict.fromkeys(t['symbol']for t in a['trade_log']))})
    with(OUT/'comparison.csv').open('w')as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    for row in rows:print(json.dumps(row,ensure_ascii=False),flush=True)
    print('BENCHMARKS',json.dumps({k:round(v['return_pct'],4)for k,v in benchmarks.items()}),flush=True)


if __name__=='__main__':main()
