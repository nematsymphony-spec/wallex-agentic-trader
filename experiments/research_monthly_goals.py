"""Periodic 30-day goals with compounding, continuous positions and risk controls."""
import csv
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import research30 as engine
import research_long_horizons as ext
from wallex_agent.clients import WallexPublic

OUT=Path('results/research-monthly-goals')
MINUTES=Path('data/research-monthly-goals/minutes')


def main():
    OUT.mkdir(parents=True,exist_ok=True);MINUTES.mkdir(parents=True,exist_ok=True)
    r=ext.make_research()
    original=json.loads(Path('results/research90-frozen/results.json').read_text())['frozen_configs']
    configs={}
    for goal in(3,5):
        for mode in('whole','block','off'):
            configs[f'goal{goal}-{mode}']={**original[str(goal)],'id':f'monthly-goal{goal}-{mode}',
                      'goal_reset_hours':720,'portfolio_loss_reset':mode,'rank_without_entry_volume':True}
    ext.dump(OUT/'plan.json',{'created_at_utc':datetime.now(timezone.utc).isoformat(),
         'configs':configs,'periods_days':[30,60,90],
         'monthly_definition':'successive 30-day blocks anchored at each experiment start, not calendar months',
         'goal_reference':'net liquidation equity at block start; no deposits or withdrawals',
         'carry_positions':'no forced boundary liquidation; entry price stop and 48h age preserved',
         'portfolio_stop_modes':{'whole':'5% against original initial equity; persistent across all blocks',
                                 'block':'5% against block opening equity, reset at block boundary',
                                 'off':'no account-level 5% stop; 5% position stop and 2% daily new-buy pause retained'},
         'costs':'0.3% each side plus assumed 20bps full spread; same frozen selection/entry rules',
         'data':'same 62 current permitted TMN spot markets; historical ranking proxy remains',
         'new_parameter_search':False})
    micro={};raw_days={}
    def load():
        for folder in(Path('data/research30/minutes'),ext.DATA/'minutes',MINUTES):
            for p in folder.glob('*.json'):
                x=json.loads(p.read_text())
                if 'day'not in x:continue
                s,day=x['symbol'],x['day'];d=x['response'];raw_days[s,day]=x
                by={}
                for j,t in enumerate(d.get('t',[])):
                    if float(d['v'][j])>0:by.setdefault(t//3600*3600,[]).append({'ts':t,**{k:float(d[k][j])for k in 'ohlcv'}})
                for t in range(day,day+86400,3600):
                    i=(t-ext.WARMUP)//3600
                    if not 0<=i<len(r.bars[s]['a']):continue
                    parts=sorted(by.get(t,[]),key=lambda z:z['ts'])
                    if not r.bars[s]['real'][i]or math.isclose(sum(z['v']for z in parts),float(r.bars[s]['a'][i,4]),rel_tol=1e-6,abs_tol=1e-6):micro[s,i]=parts
    runs={};unresolved=[]
    for iteration in range(15):
        load();required=set();runs={}
        for days in(30,60,90):
            for name,cfg in configs.items():
                for path in('OLHC','OHLC'):
                    req=set();res=r.simulate(cfg,start=ext.END-days*86400,end=ext.END,path=path,trace=True,micro=micro,requirements=req)
                    res['minute_unresolved']=[{'symbol':s,'hour':ext.WARMUP+i*3600}for s,i in sorted(req-set(micro))]
                    runs[f'{days}d-{name}-{path}']=res;required|=req
        missing=required-set(micro)
        needed={(s,(ext.WARMUP+i*3600)//86400*86400)for s,i in missing}-set(raw_days)
        print('monthly minute iteration',iteration,'hours',len(required),'unresolved',len(missing),'new symbol-days',len(needed),flush=True)
        if not missing:break
        if not needed:
            unresolved=[{'symbol':s,'hour':ext.WARMUP+i*3600}for s,i in sorted(missing)];break
        def fetch(pair):
            s,day=pair
            for attempt in range(3):
                try:
                    raw=WallexPublic().get('/v1/udf/history',{'symbol':s,'resolution':'1','from':day,'to':day+86399})
                    if raw.get('s')not in('ok','no_data'):raise ValueError('bad minute data')
                    ext.dump(MINUTES/f'{s}-{day}.json',{'symbol':s,'day':day,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'response':raw});return
                except Exception:
                    if attempt==2:raise
        with ThreadPoolExecutor(max_workers=4)as ex:list(ex.map(fetch,sorted(needed)))
    ext.dump(OUT/'results.json',{'runs':runs,'unresolved_hours':unresolved,'configs':configs})
    rows=[];blocks=[];trades=[]
    for days in(30,60,90):
        for name,cfg in configs.items():
            a=runs[f'{days}d-{name}-OLHC'];b=runs[f'{days}d-{name}-OHLC']
            row={'days':days,'goal_pct':cfg['profit_goal']*100,'risk_mode':cfg['portfolio_loss_reset'],
                 'return_pct':a['return_pct'],'other_path_return_pct':b['return_pct'],
                 'pnl_tmn':a['pnl_tmn'],'final_tmn':a['final_tmn'],'trades':a['trades'],
                 'max_drawdown_pct':a['max_drawdown_pct'],'fees_tmn':a['fees_tmn'],
                 'goal_hit_blocks':sum(v['profit_goal_hit']for v in a['monthly_blocks']),
                 'total_blocks':len(a['monthly_blocks']),'minute_unresolved_count':len(a['minute_unresolved'])}
            rows.append(row)
            blocks.extend({'days':days,'goal_pct':cfg['profit_goal']*100,'risk_mode':cfg['portfolio_loss_reset'],'block':j+1,**v}for j,v in enumerate(a['monthly_blocks']))
            trades.extend({'days':days,'goal_pct':cfg['profit_goal']*100,'risk_mode':cfg['portfolio_loss_reset'],**t}for t in a['trade_log'])
            print(json.dumps(row),flush=True)
    for filename,table in [('comparison.csv',rows),('blocks.csv',blocks),('trades.csv',trades)]:
        with(OUT/filename).open('w')as f:
            w=csv.DictWriter(f,fieldnames=list(table[0]));w.writeheader();w.writerows(table)
    # Monthly equity values must chain without hidden deposits or resets to 5m.
    for res in runs.values():
        bs=res['monthly_blocks'];assert abs(bs[0]['start_equity_tmn']-5_000_000)<1e-6
        for a,b in zip(bs,bs[1:]):assert abs(a['end_equity_tmn']-b['start_equity_tmn'])<1e-6
        compounded=math.prod(1+z['return_pct']/100 for z in bs)
        assert abs(compounded-res['final_tmn']/5_000_000)<1e-9
        assert abs(sum(t['pnl_tmn']for t in res['trade_log'])-res['pnl_tmn'])<1e-5
    ext.dump(OUT/'verification.json',{'cash_chained_across_blocks':True,'compounding_reconciled':True,
             'no_hidden_deposits':True,'trade_pnl_reconciled':True,'config_search_performed':False,
             'unresolved_hours':unresolved})


if __name__=='__main__':main()
