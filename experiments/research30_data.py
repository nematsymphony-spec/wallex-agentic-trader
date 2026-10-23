"""Acquire public data and freeze a competition-eligible research universe."""
from __future__ import annotations
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from wallex_agent.clients import WallexPublic

ROOT = Path('data/research30')
END = int(datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp())
START = END-30*86400
TRAIN = START-30*86400
WARMUP = TRAIN-8*86400


def save(path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(x, ensure_ascii=False, separators=(',', ':')))


def acquire():
    ROOT.mkdir(parents=True, exist_ok=True)
    api = WallexPublic()
    markets = json.loads(Path('work/research30/markets-current.json').read_text())['result']['markets']
    package = json.loads(Path('work/research30/package-current.json').read_text())['result']
    banned = {s.replace('/', '') for s in package['unavailableMarkets']}
    banned |= {s.replace('/', '') for s in json.loads(Path('config/package-183-exclusions.json').read_text())['unavailable_markets']}
    universe = [m for m in markets if m.get('is_spot') and m['quote_asset'] == 'TMN' and m['symbol'] not in banned]
    save(ROOT/'universe.json', {'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                              'banned': sorted(banned), 'markets': universe})
    daily_old = Path('/Users/savior/Documents/Codex/2026-10-23/new-chat-2/work/historical-daily.json')
    hourly_old = daily_old.parent/'walk-hourly-merged.json'
    cache = json.loads(hourly_old.read_text())
    queries = []
    for meta in universe:
        s = meta['symbol']
        for kind, resolution in [('daily', '1D'), ('hourly', '60')]:
            dest = ROOT/f'{s}-{kind}.json'
            if dest.exists():
                continue
            if kind == 'hourly':
                old = {int(t): b for t, b in cache.get(s, {}).items() if WARMUP <= int(t) < END}
                # Reuse already captured public candles, with original file provenance.
                save(ROOT/f'{s}-hourly-seed.json', {'source_file': str(hourly_old), 'bars': old})
                # Re-fetch whole chunks that have no data; individual holes are audited against daily volume.
                for lo in range(WARMUP, END, 20*86400):
                    hi = min(END, lo+20*86400)
                    count = sum(lo <= t < hi for t in old)
                    if count < (hi-lo)//3600*.95:
                        queries.append((s, kind, resolution, lo, hi))
            else:
                for lo in range(WARMUP, END, 30*86400):
                    queries.append((s, kind, resolution, lo, min(END, lo+30*86400)))
    print('eligible markets', len(universe), 'new history queries', len(queries), flush=True)

    def fetch(q):
        s, kind, resolution, lo, hi = q
        dest = ROOT/'raw'/f'{s}-{kind}-{lo}-{hi}.json'
        if dest.exists():
            return dest, None
        for attempt in range(3):
            try:
                raw = api.get('/v1/udf/history', {'symbol': s, 'resolution': resolution, 'from': lo, 'to': hi-1})
                if raw.get('s') not in ('ok', 'no_data'):
                    raise ValueError('bad candle status')
                save(dest, {'query': q, 'captured_at_utc': datetime.now(timezone.utc).isoformat(), 'response': raw})
                return dest, None
            except Exception as e:
                if attempt == 2:
                    return dest, f'{type(e).__name__}: {e}'
                time.sleep(attempt+1)
    errors = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs = [ex.submit(fetch, q) for q in queries]
        for count, f in enumerate(as_completed(fs), 1):
            dest, err = f.result()
            if err: errors.append({'path': str(dest), 'error': err})
            if count % 25 == 0 or count == len(fs):
                print('history complete', count, '/', len(fs), 'errors', len(errors), flush=True)
    save(ROOT/'fetch-errors.json', errors)
    for meta in universe:
        s = meta['symbol']
        for kind in ('daily', 'hourly'):
            rows = {}
            seed = ROOT/f'{s}-hourly-seed.json'
            if kind == 'hourly' and seed.exists():
                rows.update(json.loads(seed.read_text())['bars'])
            for path in sorted((ROOT/'raw').glob(f'{s}-{kind}-*.json')):
                r = json.loads(path.read_text())['response']
                if r.get('s') != 'ok': continue
                for i, t in enumerate(r['t']):
                    if WARMUP <= t < END:
                        rows[str(t)] = {k: float(r[k][i]) for k in 'ohlcv'}
            save(ROOT/f'{s}-{kind}.json', rows)
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in ROOT.glob('*.json') if p.name != 'manifest.json'}
    save(ROOT/'manifest.json', {'start': START, 'end': END, 'train_start': TRAIN,
                                'warmup': WARMUP, 'hashes': manifest})
    print('DATA_READY', len(manifest), 'files', flush=True)


if __name__ == '__main__':
    acquire()
