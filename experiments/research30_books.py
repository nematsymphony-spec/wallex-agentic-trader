"""Three public-book snapshots; executable immediate roundtrip, not future exit."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from wallex_agent.clients import WallexPublic

ROOT=Path('data/research30')


def consume_asks(levels, budget):
    quantity=spent=0.
    for p,q in levels:
        take=min(q, max(0.,budget-spent)/p)
        quantity+=take;spent+=take*p
        if spent>=budget*(1-1e-10):break
    return quantity,spent,spent>=budget*(1-1e-8)


def consume_bids(levels, quantity):
    sold=value=0.
    for p,q in levels:
        take=min(q,max(0.,quantity-sold));sold+=take;value+=take*p
        if sold>=quantity*(1-1e-10):break
    return value,sold>=quantity*(1-1e-8)


def measure(raw, capital, fee=.003):
    asks=sorted([(float(r['price']),float(r['quantity'])) for r in raw['ask'] if float(r['quantity'])>0])
    bids=sorted([(float(r['price']),float(r['quantity'])) for r in raw['bid'] if float(r['quantity'])>0],reverse=True)
    if not asks or not bids or asks[0][0]<bids[0][0]:return {'valid':False}
    q,spent,ok=consume_asks(asks,capital/(1+fee))
    proceeds,sellok=consume_bids(bids,q)
    # Also isolate spread+depth with no fee, allocating the same initial capital.
    q0,spent0,ok0=consume_asks(asks,capital)
    out0,sellok0=consume_bids(bids,q0)
    return {'valid':bool(ok and sellok and ok0 and sellok0),
            'spread_pct':(asks[0][0]-bids[0][0])/((asks[0][0]+bids[0][0])/2)*100,
            'buy_vwap':spent/q if q else None,'sell_vwap':proceeds/q if q else None,
            'quantity':q,'spent_excluding_fee':spent,
            'net_immediate_sale_tmn':proceeds*(1-fee),
            'roundtrip_cost_pct':(1-proceeds*(1-fee)/capital)*100 if ok and sellok else None,
            'spread_depth_only_cost_pct':(1-out0/capital)*100 if ok0 and sellok0 else None,
            'buy_fee_tmn':spent*fee,'sell_fee_tmn':proceeds*fee}


def main():
    syms=[m['symbol'] for m in json.loads((ROOT/'universe.json').read_text())['markets']]
    for n in range(3):
        dest=ROOT/f'books-round-{n}.json'
        if dest.exists():continue
        def get(s):
            try:return {'symbol':s,'captured_at_utc':datetime.now(timezone.utc).isoformat(),
                        'book':WallexPublic().depth(s)}
            except Exception as e:return {'symbol':s,'error':type(e).__name__}
        with ThreadPoolExecutor(max_workers=4) as ex:rows=list(ex.map(get,syms))
        dest.write_text(json.dumps(rows,separators=(',',':')))
        print('book round',n+1,'complete',len(rows),flush=True)
        if n<2:time.sleep(20)
    results={}
    for s in syms:
        results[s]={}
        for capital in (5_000_000,10_000_000,50_000_000,100_000_000):
            xs=[]
            for n in range(3):
                row=next(r for r in json.loads((ROOT/f'books-round-{n}.json').read_text()) if r['symbol']==s)
                if 'book' in row:xs.append({'at':row['captured_at_utc'],**measure(row['book'],capital)})
            results[s][str(capital)]=xs
    (ROOT/'book-metrics.json').write_text(json.dumps(results,separators=(',',':')))


if __name__=='__main__':main()
