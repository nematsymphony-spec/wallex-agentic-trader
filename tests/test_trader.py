import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from wallex_agent.clients import OpenAIDecider
from wallex_agent.core import Decision, MarketView, Portfolio, Position, RiskSettings, canonical_hash, risk_check
from wallex_agent.engine import AuditLog, Engine, run_backtest


def view(symbol="BTCTMN", allowed=True, spread=D("10")):
    bid = D(100)
    ask = bid * (D(1) + spread / D(10000))
    return MarketView(symbol, 1_790_000_000, D(100), bid, ask, D(5_000_000), D(5_000_000), D("0.02"), D("0.03"), D("0.01"), D(10_000_000), True, allowed)


class AlwaysBuy:
    def decide(self, market, has_position):
        return Decision("BUY", market.symbol, "Test decision with explicit signal", D("0.8"), ["momentum_3"], "fake", canonical_hash(market.model_input()))


class GuardrailTests(unittest.TestCase):
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

    def test_thin_exit_book_blocks_buy(self):
        v = view()
        v = MarketView(v.symbol, v.timestamp, v.close, v.bid, v.ask, D(350_000), v.ask_quote_depth, v.momentum_3, v.momentum_12, v.volatility_12, v.volume_quote, True, True)
        p = Portfolio(RiskSettings())
        self.assertEqual(risk_check(AlwaysBuy().decide(v, False), v, p, p.settings, {v.symbol: v.bid})[1], "insufficient_two_sided_depth")


class AuditTests(unittest.TestCase):
    def test_backtest_fills_at_next_open(self):
        class FakePublic:
            def markets(self):
                return {"BTCTMN": {"spot_active": True}}

            def unavailable(self, package_id):
                return set()

            def candles(self, symbol, start, end):
                rows = [{"timestamp": 1000 + i * 3600, "open": D(100), "high": D(100), "low": D(100), "close": D(100), "volume": D(100)} for i in range(14)]
                rows[-1]["open"] = D(150)
                return rows

        with tempfile.TemporaryDirectory() as tmp, patch("wallex_agent.engine.WallexPublic", FakePublic), patch("wallex_agent.engine.RuleDecider", AlwaysBuy):
            db = str(Path(tmp) / "test.db")
            run_backtest(db, 1000 + 13 * 3600, 1000 + 14 * 3600)
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
