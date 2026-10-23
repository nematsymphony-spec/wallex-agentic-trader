"""Benchmarks and historical selection/randomization sensitivity, not new holdout."""
import json
import numpy as np
from research30 import Research, OUT, ROOT, START, END, TRAIN, WARMUP, HOUR, dump


def main():
    r=Research();original=r.choose
    benchmarks={}
    for s in ('BTCTMN','PUMPTMN','BCHTMN'):
        first=(START-WARMUP)//HOUR
        while not r.bars[s]['real'][first]:first+=1
        r.choose=lambda cfg,i:s if i==first else None
        cfg={'id':'buy_hold_'+s,'family':'benchmark','hold':10000,'stop_kind':'fixed','stop':.99,'tp':0.}
        benchmarks[s]=r.simulate(cfg,guards=False,trace=True)
    r.choose=original
    summary=json.loads((OUT/'summary.json').read_text())
    cfg=summary['selected_on_previous_month']['relative_strength']['config']
    random={};rolling={}
    for goal in (.03,.05):
        cg={**cfg,'profit_goal':goal}
        rs=[]
        for seed in range(300):
            x=r.simulate({**cg,'id':f'random-{goal}-{seed}','random_choice':True,'_random_seed':seed})
            rs.append({'seed':seed,'return_pct':x['return_pct'],'goal_hit':x['profit_goal_hit'],'trades':x['trades']})
        random[str(goal)]={'type':'random symbol among same eligible relative-strength candidates; identical costs, timing and guards',
                           'n_seeds':len(rs),'positive_count':sum(x['return_pct']>0 for x in rs),
                           'goal_hit_count':sum(x['goal_hit'] for x in rs),
                           'median_return_pct':float(np.median([x['return_pct'] for x in rs])),
                           'min_return_pct':min(x['return_pct'] for x in rs),'max_return_pct':max(x['return_pct'] for x in rs),
                           'runs':rs}
        windows=[]
        for start in range(TRAIN,START+1,86400):
            x=r.simulate(cg,start=start,end=start+30*86400)
            windows.append({'start':start,'end':start+30*86400,'return_pct':x['return_pct'],
                            'goal_hit':x['profit_goal_hit'],'portfolio_stop':x['portfolio_stop'],'trades':x['trades']})
        rolling[str(goal)]={'overlapping_windows':len(windows),
                            'positive_count':sum(x['return_pct']>0 for x in windows),
                            'goal_hit_count':sum(x['goal_hit'] for x in windows),
                            'median_return_pct':float(np.median([x['return_pct'] for x in windows])),
                            'worst_return_pct':min(x['return_pct'] for x in windows),'runs':windows}
    dump(OUT/'controls.json',{'benchmarks':benchmarks,'randomized_same_candidates':random,'rolling_30_day_windows':rolling,
                             'limitations':['The rolling windows overlap and are not independent trials',
                                            'All controls are on already examined historical data',
                                            'Controls use hourly OHLC paths, no historical books or actual latency',
                                            'Randomization measures symbol ranking sensitivity, not future probability of profit']})
    print('benchmarks', {s:round(x['return_pct'],3)for s,x in benchmarks.items()},flush=True)
    print('random', {g:{k:v for k,v in x.items()if k!='runs'}for g,x in random.items()},flush=True)
    print('rolling', {g:{k:v for k,v in x.items()if k!='runs'}for g,x in rolling.items()},flush=True)


if __name__=='__main__':main()
