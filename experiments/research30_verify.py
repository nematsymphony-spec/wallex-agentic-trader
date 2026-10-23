"""Adaptive minute replay of family champions and objective-limited candidates."""
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from research30 import Research, OUT, ROOT, START, END, TRAIN, WARMUP, HOUR, dump
from wallex_agent.clients import WallexPublic


def main():
    r=Research()
    summary=json.loads((OUT/'summary.json').read_text())
    rows=json.loads((OUT/'all-scenarios.json').read_text())
    selected={v['id']:v['config'] for v in summary['selected_on_previous_month'].values()}
    selected.update({v['id']:v['config'] for v in summary['best_seen_in_target_month_discovery'].values()})
    # Include 3% and 5% versions of the relative-strength champion, and its
    # no-profit-cap counterpart. These are policies, not different market windows.
    for row in rows:
        if row['id'] in ('relative_strength-007','relative_strength-007-goal3','relative_strength-007-goal5'):
            selected[row['id']]=row['config']
    # All AI models were rejected on preceding validation, so remain HOLD. No
    # post-hoc refitting on target-month outcomes is introduced for replay.
    micro={};raw_days={};required=set();runs={};errors=[]
    def load_days():
        for p in (ROOT/'minutes').glob('*.json'):
            x=json.loads(p.read_text())
            if 'day' not in x:continue
            s=x['symbol'];day=x['day'];raw=x['response']
            raw_days[s,day]=x
            minutes={}
            for j,t in enumerate(raw.get('t',[])):
                minutes[int(t)]={'ts':int(t),**{k:float(raw[k][j]) for k in 'ohlcv'}}
            for t in range(day,day+86400,HOUR):
                i=(t-WARMUP)//HOUR
                if not 0<=i<len(r.bars[s]['a']):continue
                parts=[z for tt,z in minutes.items() if t<=tt<t+HOUR and z['v']>0]
                parts.sort(key=lambda z:z['ts'])
                hourly=r.bars[s]['a'][i]
                real=r.bars[s]['real'][i]
                volume=sum(z['v'] for z in parts)
                if not real or math.isclose(volume,float(hourly[4]),rel_tol=1e-6,abs_tol=1e-6):micro[s,i]=parts
    (ROOT/'minutes').mkdir(exist_ok=True)
    for iteration in range(12):
        load_days();required=set();runs={}
        for key,cfg in selected.items():
            for path in ('OLHC','OHLC'):
                if cfg['family']=='ai_filter':continue
                local_required=set()
                result=r.simulate(cfg,path=path,trace=True,micro=micro,requirements=local_required)
                result['minute_required_hours']=len(local_required)
                result['minute_unresolved']=[{'symbol':s,'hour':WARMUP+i*HOUR}for s,i in sorted(local_required-set(micro))]
                required|=local_required
                runs[key+'-'+path]=result
        missing=required-set(micro)
        needed={(s,(WARMUP+i*HOUR)//86400*86400) for s,i in missing}
        needed-=set(raw_days)
        print('minute iteration',iteration,'required hours',len(required),'unresolved',len(missing),'new symbol-days',len(needed),flush=True)
        if not missing:break
        if not needed:
            errors=[{'symbol':s,'hour':WARMUP+i*HOUR} for s,i in sorted(missing)]
            break
        def fetch(pair):
            s,day=pair
            for attempt in range(3):
                try:
                    response=WallexPublic().get('/v1/udf/history',{'symbol':s,'resolution':'1','from':day,'to':day+86400-1})
                    if response.get('s') not in ('ok','no_data'):raise ValueError('bad response')
                    dump(ROOT/'minutes'/f'{s}-{day}.json',{'symbol':s,'day':day,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'response':response})
                    return
                except Exception:
                    if attempt==2:raise
        with ThreadPoolExecutor(max_workers=4) as ex:list(ex.map(fetch,sorted(needed)))
    if errors:dump(OUT/'minute-unresolved.json',errors)
    # Cost sensitivity uses the same frozen policies and minute observations.
    sensitivity={}
    for key,cfg in selected.items():
        if cfg['family']=='ai_filter':continue
        sensitivity[key]={}
        for cost,fee,spread in [('base',.003,20),('no_spread_fee_only',.003,0),('stress_25bps_extra_each_side',.003,70),('zero_cost_diagnostic',0.,0.)]:
            req=set()
            sensitivity[key][cost]=r.simulate(cfg,fee=fee,spread_bps=spread,micro=micro,trace=False,requirements=req)
            sensitivity[key][cost]['minute_unresolved']=[{'symbol':s,'hour':WARMUP+i*HOUR}for s,i in sorted(req-set(micro))]
    dump(OUT/'minute-replay.json',{'unresolved_hours':errors,'required_hours':len(required),
           'captured_symbol_days':len(raw_days),'configs':selected,'runs':runs,'sensitivity':sensitivity,
           'limitations':['Minute OHLC still leaves tick order unknown; two paths retained',
                          'No historical bid/ask, depth or real execution delay; these are candle-price simulations',
                          'Sensitivity runs may enter different hours not fully covered by minute downloads; coverage audited separately']})
    for key,cfg in selected.items():
        a=runs.get(key+'-OLHC');b=runs.get(key+'-OHLC')
        if a:print(key,'minute returns',a['return_pct'],b['return_pct'],'trades',a['trades'],'dd',a['max_drawdown_pct'],flush=True)


if __name__=='__main__':main()
