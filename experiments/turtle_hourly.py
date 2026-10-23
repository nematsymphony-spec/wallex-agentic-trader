"""Close-confirmed, long-only hourly Turtle adaptations; offline research only.

Uses the frozen prior comparison window, not a newly selected profitable window.
Original daily intrabar entries, volatility sizing and pyramiding are deliberately
replaced by hourly close confirmation, next-open execution and user-selected all-in.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from hourly_two_coins import START, END, WARMUP, HOUR, INITIAL, SYMBOLS, TEHRAN, parse, buy_hold


def channels(bars, p, length):
    """Signal bar p must not contribute to its own breakout threshold."""
    prior = bars[p-length:p]
    assert len(prior) == length
    return max(x['high'] for x in prior), min(x['low'] for x in prior)


def volatility(bars):
    tr = [b['high']-b['low'] if i == 0 else
          max(b['high']-b['low'], abs(b['high']-bars[i-1]['close']),
              abs(b['low']-bars[i-1]['close'])) for i, b in enumerate(bars)]
    out = [None]*len(bars)
    out[19] = sum(tr[:20])/20
    for i in range(20, len(bars)):
        out[i] = (19*out[i-1]+tr[i])/20
    return out


def virtual_candidates(bars, n):
    """Warm up S1's last-breakout filter using virtual long AND short trades.

    Virtual trade profitability is gross price P&L, independently of whether a
    real long trade was allowed, just as a skipped breakout must still be tracked.
    Close-confirmed channel exit executes next open; the fixed 2N stop is intrabar.
    At most one new virtual trade per bar; initially assume last trade was a loser.
    """
    side, entry, stop, previous_winner = 0, 0., 0., False
    signals = {}
    events = []
    for i in range(56, len(bars)):
        b, p = bars[i], i-1
        upper10, lower10 = channels(bars, p, 10)
        channel_exit = side and (bars[p]['close'] < lower10 if side == 1 else bars[p]['close'] > upper10)
        gap_stop = side and (b['open'] <= stop if side == 1 else b['open'] >= stop)
        if channel_exit or gap_stop:
            previous_winner = side*(b['open']-entry) > 0
            side = 0
        upper20, lower20 = channels(bars, p, 20)
        candidate = 0 if side else (1 if bars[p]['close'] > upper20 else
                                   -1 if bars[p]['close'] < lower20 else 0)
        signals[i] = {'candidate': candidate, 'previous_winner': previous_winner}
        if candidate:
            side, entry = candidate, b['open']
            stop = entry-side*2*n[p]
            events.append({'ts': b['ts'], 'side': side, 'previous_winner': previous_winner})
        if side and (b['low'] <= stop if side == 1 else b['high'] >= stop):
            # After an open entry, a stop touch is necessarily after that entry.
            previous_winner = side*(stop-entry) > 0
            side = 0
    return signals, events


def run(bars, system, fee=.003, spread_bps=20):
    n = volatility(bars)
    virtual, virtual_events = virtual_candidates(bars, n)
    first, last = (START-WARMUP)//HOUR, (END-WARMUP)//HOUR
    cash, qty, basis, entry_ts, entry_fill, stop = INITIAL, 0., 0., None, None, None
    peak, dd, fees = INITIAL, 0., 0.
    day, day_start, daily_stop, total_stop = None, INITIAL, False, False
    half = spread_bps/20_000
    trades, decisions = [], []
    exit_length = 20 if system == 's2_55_20' else 10

    def sell(raw_price, ts, reason):
        nonlocal cash, qty, fees
        fill = raw_price*(1-half)
        proceeds = qty*fill
        exit_fee = proceeds*fee
        cash += proceeds-exit_fee
        fees += exit_fee
        trades.append({'entry_ts': entry_ts, 'exit_ts': ts, 'entry_fill': entry_fill,
                       'exit_fill': fill, 'quantity': qty, 'stop_raw_price': stop,
                       'reason': reason, 'pnl_tmn': cash-basis})
        qty = 0.

    for i in range(first, last):
        b, p = bars[i], i-1
        marked_open = cash+qty*b['open']*(1-half)*(1-fee)
        local_day = datetime.fromtimestamp(b['ts'], TEHRAN).date()
        if local_day != day:
            day, day_start, daily_stop = local_day, marked_open, False
        daily_stop |= marked_open <= day_start*.98
        total_stop |= marked_open <= INITIAL*.95
        _, lower_exit = channels(bars, p, exit_length)
        exited = False
        if qty and (total_stop or b['open'] <= stop or bars[p]['close'] < lower_exit):
            reason = 'portfolio_stop_next_open' if total_stop else ('2N_gap_stop' if b['open'] <= stop else 'channel_exit')
            sell(b['open'], b['ts'], reason)
            exited = True
        upper20, _ = channels(bars, p, 20)
        upper55, _ = channels(bars, p, 55)
        breaks20, breaks55 = bars[p]['close'] > upper20, bars[p]['close'] > upper55
        v = virtual[i]
        if system == 's1_20_10_filtered':
            signal = (v['candidate'] == 1 and not v['previous_winner']) or breaks55
            reason = '55h_failsafe' if breaks55 and v['previous_winner'] else '20h_filtered_breakout'
        elif system == 's1_20_10_unfiltered':
            signal, reason = breaks20, '20h_unfiltered_breakout'
        elif system == 's2_55_20':
            signal, reason = breaks55, '55h_breakout'
        else:
            raise ValueError(system)
        if not qty and not exited and signal:
            decisions.append({'ts': b['ts'], 'signal_bar_ts': bars[p]['ts'],
                              'N': n[p], 'reason': reason, 'previous_virtual_winner': v['previous_winner'],
                              'blocked': bool(daily_stop or total_stop)})
            if not daily_stop and not total_stop:
                basis, entry_ts = cash, b['ts']
                size = cash/(1+fee)
                entry_fill = b['open']*(1+half)
                qty = size/entry_fill
                fees += size*fee
                cash = 0.
                # Stop measured from actual entry fill; trigger uses raw traded prices.
                stop = entry_fill-2*n[p]
        if qty and b['low'] <= stop:
            sell(min(b['open'], stop), b['ts'], '2N_intrahour_stop')
            # OHLC timestamps identify the stop hour, not the precise second.
        equity = cash+qty*b['close']*(1-half)*(1-fee)
        peak = max(peak, equity)
        dd = max(dd, 1-equity/peak)
        daily_stop |= equity <= day_start*.98
        total_stop |= equity <= INITIAL*.95
    if qty:
        sell(bars[last-1]['close'], END, 'end_of_test')
        peak = max(peak, cash)
        dd = max(dd, 1-cash/peak)
    return {'net_return_pct': (cash/INITIAL-1)*100, 'net_pnl_tmn': cash-INITIAL,
            'final_equity_tmn': cash, 'max_drawdown_pct': dd*100,
            'trades': len(trades), 'wins': sum(t['pnl_tmn'] > 0 for t in trades),
            'fees_tmn': fees, 'total_stop_triggered': total_stop,
            'trade_log': trades, 'entry_decisions': decisions,
            'virtual_breakout_events': virtual_events}


def main():
    path = Path('results/hourly-btc-pump-data.json')
    data = json.loads(path.read_text())
    if (data['start'], data['end'], data['warmup']) != (START, END, WARMUP):
        raise ValueError('wrong data window')
    bars = {s: parse(data['symbols'][s]) for s in SYMBOLS}
    results = {}
    for label, fee, spread in [('base', .003, 20), ('zero_cost_diagnostic', 0., 0.)]:
        results[label] = {}
        for symbol in SYMBOLS:
            results[label][symbol] = {'buy_hold_all_in': buy_hold(bars[symbol], fee, spread)}
            for system in ('s1_20_10_filtered', 's1_20_10_unfiltered', 's2_55_20'):
                results[label][symbol][system] = run(bars[symbol], system, fee, spread)
    out = {'kind': 'offline_historical_replay_hourly_turtle_adaptations',
           'data_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
           'start_utc': datetime.fromtimestamp(START, timezone.utc).isoformat(),
           'end_utc': datetime.fromtimestamp(END, timezone.utc).isoformat(),
           'starting_capital_per_independent_coin_tmn': INITIAL,
           'rules': {'entry': 'completed close strictly beyond preceding 20 or 55 hourly highs; next open fill',
                     'exit': 'completed close below preceding 10 or 20 hourly lows; next open fill',
                     'stop': 'fixed entry fill minus 2N; intrahour low touch, gaps filled at open',
                     'N': '20-bar Wilder smoothing of TR, seeded with first 20-bar arithmetic mean',
                     'S1_filter': 'track virtual long and short 20/10 breakouts during warmup and test; skip after gross winner; 55h failsafe',
                     'allocation': 'all available cash, fee reserved, no leverage, short sales or pyramiding',
                     'portfolio_guards': 'same 2% Tehran-day new-buy pause and 5% initial-equity stop at next open as earlier hourly tests'},
           'costs': {'fee_each_side': .003, 'full_spread_bps_assumed': 20, 'credit_package_fee_tmn': 0,
                     'fee_evidence': 'config/package-183-fees-observed.json'},
           'limitations': ['57h exploratory window with 168h indicator warmup, no parameter search or held-out test',
                           'This is an hourly close-confirmed long-only all-in adaptation, not the original daily intrabar futures system',
                           'Historical spread, depth, actual slippage, fill availability, precision and minimum-order rules not modeled',
                           'Drawdown sampled hourly at closes; stop executions included but other intrahour equity extremes absent',
                           '2N stop fills optimistic if book lacks liquidity; OHLC gives stop hour not exact execution time',
                           'Virtual filter starts with prior breakout classified as loser; finite warmup may affect state',
                           'S1 failsafe uses 10h exit in this specified adaptation; all exits forced at test end',
                           'Zero-cost scenario is a diagnostic, not an available trading fee'],
           'results': results}
    dest = Path('results/turtle-hourly-btc-pump-57h.json')
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n')
    for cost, coins in results.items():
        print(cost)
        for coin, systems in coins.items():
            for system, r in systems.items():
                print(f"{coin} {system:24s} return={r['net_return_pct']:.4f}% pnl={r['net_pnl_tmn']:.0f} dd={r['max_drawdown_pct']:.4f}% trades={r['trades']} wins={r['wins']}")


if __name__ == '__main__':
    main()
