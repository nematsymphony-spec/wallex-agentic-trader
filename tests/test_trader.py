import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from wallex_agent.clients import OpenAIDecider, WallexPublic
from wallex_agent.core import Decision, MarketView, Portfolio, Position, RiskSettings, canonical_hash, risk_check
from wallex_agent.engine import AuditLog, Engine, features, run_backtest
from wallex_agent.selector import GlobalOpenAIDecider, gate, rank_views


def view(symbol="BTCTMN", allowed=True, spread=D("10")):
    bid = D(100)
    ask = bid * (D(1) + spread / D(10000))
    return MarketView(symbol, 1_790_000_000, D(100), bid, ask, D(5_000_000), D(5_000_000), D("0.02"), D("0.03"), D("0.01"), D(10_000_000), True, allowed)


class AlwaysBuy:
    def decide(self, market, has_position):
        return Decision("BUY", market.symbol, "Test decision with explicit signal", D("0.8"), ["momentum_3"], "fake", canonical_hash(market.model_input()))


class GuardrailTests(unittest.TestCase):
    def test_package_exclusion_snapshot_survives_incomplete_api_list(self):
        api = WallexPublic()
        with patch.object(api, "get", return_value={"result": {"detail": {"id": 183}, "unavailableMarkets": []}}):
            forbidden = api.unavailable(183)
        self.assertIn("SOONTMN", forbidden)
        self.assertIn("1BBABYDOGEUSDT", forbidden)
        self.assertNotIn("PUMPTMN", forbidden)

    def test_six_complete_twelve_hour_windows_use_three_day_weights(self):
        close = D(100)
        bars = [{"timestamp": 0, "open": close, "close": close, "volume": D(1)}]
        for i in range(72):
            close *= D(1) + D(i // 12 + 1) / D(100)
            bars.append({"timestamp": (i + 1) * 3600, "open": close, "close": close, "volume": D(1)})
        v = features("BTCTMN", bars, close, close * D("1.001"), D(10_000_000), D(10_000_000), True, True)
        expected = D(12).sqrt() * (D("0.5") * D("0.055") + D("0.3") * D("0.035") + D("0.2") * D("0.015"))
        self.assertLess(abs(v.volatility_3d_12h - expected), D("1e-20"))
        bars[-2]["timestamp"] -= 1
        self.assertEqual(features("BTCTMN", bars, close, close * D("1.001"), D(10_000_000), D(10_000_000), True, True).volatility_3d_12h, D(0))

    def test_global_ai_rejects_symbol_outside_shortlist(self):
        v = replace(view(), volatility_3d_12h=D("0.02"), volume_quote=D(100_000_000))
        decider = GlobalOpenAIDecider("test-key")
        invalid = {"action": "BUY", "symbol": "WRONGTMN", "reason": "Unsupported symbol was selected", "confidence": 0.8, "signal_ids": ["momentum_3"]}
        with patch("wallex_agent.selector.request_json", return_value={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(invalid)}]}]}):
            self.assertEqual(decider.decide_all([v], [], set()).action, "HOLD")

    def test_global_ai_selects_one_eligible_market_from_full_list(self):
        candidates = [replace(view(s), volatility_3d_12h=D("0.02"), volume_quote=D(100_000_000)) for s in ("BTCTMN", "PUMPTMN", "USOONTMN")]
        ranked, _ = rank_views(candidates, RiskSettings(), candidates[0].timestamp)
        selected = {"action": "BUY", "symbol": "PUMPTMN", "reason": "Positive momentum with acceptable costs", "confidence": 0.7, "signal_ids": ["momentum_3", "spread_bps"]}
        with patch("wallex_agent.selector.request_json", return_value={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(selected)}]}]}) as request:
            decision = GlobalOpenAIDecider("test-key").decide_all(candidates, ranked, set())
        self.assertEqual((decision.action, decision.symbol), ("BUY", "PUMPTMN"))
        self.assertEqual({x["symbol"] for x in json.loads(request.call_args.kwargs["payload"]["input"])["candidates"]}, {v.symbol for v in candidates})

    def test_volatility_is_bounded_ranking_bonus(self):
        a = replace(view("ATMN"), volatility_3d_12h=D("0.01"), volume_quote=D(100_000_000))
        b = replace(view("BTMN"), volatility_3d_12h=D("0.10"), volume_quote=D(100_000_000))
        ranked, reasons = rank_views([a, b], RiskSettings(), a.timestamp)
        self.assertEqual(set(reasons.values()), {"eligible"})
        scores = {x["symbol"]: x["score"] for x in ranked}
        self.assertLessEqual(scores["BTMN"] - scores["ATMN"], D("0.25"))

    def test_disallowed_market_never_orders(self):
        p = Portfolio(RiskSettings())
        v = view(allowed=False)
        decision = AlwaysBuy().decide(v, False)
        self.assertEqual(risk_check(decision, v, p, p.settings, {v.symbol: v.bid})[1], "market_not_allowed")

    def test_position_and_drawdown_limits(self):
        p = Portfolio(RiskSettings())
        v = view()
        decision = AlwaysBuy().decide(v, False)
        intent, _ = risk_check(decision, v, p, p.settings, {v.symbol: v.bid})
        self.assertLessEqual(intent.notional_tmn, D(1_000_000))
        p.positions = {"ETHTMN": Position("ETHTMN", D(1), D(100)), "SOLTMN": Position("SOLTMN", D(1), D(100))}
        self.assertEqual(risk_check(decision, v, p, p.settings, {v.symbol: v.bid})[1], "max_open_positions")
        p.positions.clear()
        p.daily_stop = True
        self.assertEqual(risk_check(decision, v, p, p.settings, {v.symbol: v.bid})[1], "daily_drawdown_stop")
        p.daily_stop = False
        p.total_stop = True
        self.assertEqual(risk_check(decision, v, p, p.settings, {v.symbol: v.bid})[1], "total_drawdown_stop")

    def test_bad_model_output_fails_closed(self):
        decider = OpenAIDecider("test-key")
        with patch("wallex_agent.clients.request_json", return_value={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"action": "BUY", "symbol": "WRONG"})}]}]}):
            self.assertEqual(decider.decide(view(), False).action, "HOLD")

    def test_spread_filter(self):
        v = view(spread=D(100))
        p = Portfolio(RiskSettings())
        self.assertEqual(risk_check(AlwaysBuy().decide(v, False), v, p, p.settings, {v.symbol: v.bid})[1], "spread_too_wide")
        p.positions[v.symbol] = Position(v.symbol, D(2000), D(200_000))
        sell = Decision("SELL", v.symbol, "Reduce an existing position despite spread", D("0.8"), ["spread_bps"], "fake", "hash")
        self.assertEqual(risk_check(sell, v, p, p.settings, {v.symbol: v.bid})[1], "approved")

    def test_thin_exit_book_blocks_buy(self):
        v = view()
        v = MarketView(v.symbol, v.timestamp, v.close, v.bid, v.ask, D(350_000), v.ask_quote_depth, v.momentum_3, v.momentum_12, v.volatility_12, v.volume_quote, True, True)
        p = Portfolio(RiskSettings())
        self.assertEqual(risk_check(AlwaysBuy().decide(v, False), v, p, p.settings, {v.symbol: v.bid})[1], "insufficient_two_sided_depth")

    def test_position_scales_down_to_two_sided_depth(self):
        v = view(symbol="PUMPTMN")
        v = MarketView(v.symbol, v.timestamp, v.close, v.bid, v.ask, D(3_000_000), D(4_000_000), v.momentum_3, v.momentum_12, v.volatility_12, v.volume_quote, True, True)
        p = Portfolio(RiskSettings())
        intent, reason = risk_check(AlwaysBuy().decide(v, False), v, p, p.settings, {v.symbol: v.bid})
        self.assertEqual(reason, "approved")
        self.assertEqual(intent.notional_tmn, D(600_000))


class AuditTests(unittest.TestCase):
    def test_backtest_fills_at_next_open(self):
        class FakePublic:
            def markets(self):
                return {"BTCTMN": {"spot_active": True, "quoteAsset": "TMN"}}

            def unavailable(self, package_id):
                return set()

            def candles(self, symbol, start, end):
                rows = [{"timestamp": 1000 + i * 3600, "open": D(100), "high": D(100), "low": D(100), "close": D(100) + D(i) / D(100), "volume": D(1_000_000)} for i in range(74)]
                rows[-1]["open"] = D(150)
                return rows

        class GlobalBuy:
            def decide_all(self, shortlist, ranked, held_symbols):
                return Decision("BUY", "BTCTMN", "Test global buy with explicit signal", D("0.8"), ["momentum_3"], "fake", "hash")

        with tempfile.TemporaryDirectory() as tmp, patch("wallex_agent.engine.WallexPublic", FakePublic), patch("wallex_agent.engine.GlobalRuleDecider", GlobalBuy):
            db = str(Path(tmp) / "test.db")
            run_backtest(db, 1000 + 73 * 3600, 1000 + 74 * 3600)
            log = AuditLog(db)
            fill = json.loads(log.connection.execute("SELECT payload FROM events WHERE kind='fill'").fetchone()[0])
            self.assertEqual(D(fill["price"]), D("150.15"))

    def test_duplicate_order_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(str(Path(tmp) / "audit.db"))
            engine = Engine(AlwaysBuy(), Portfolio(RiskSettings()), log)
            first = engine.step(view())
            second = engine.step(view())
            self.assertEqual(first.get("fill"), "simulated")
            self.assertEqual(second["risk"], "position_already_open")
            self.assertTrue(log.verify())
            log.connection.execute("UPDATE events SET payload='{}' WHERE id=1")
            log.connection.commit()
            self.assertFalse(log.verify())

    def test_live_command_is_blocked(self):
        from wallex_agent.cli import main
        self.assertEqual(main(["live"]), 3)


if __name__ == "__main__":
    unittest.main()
