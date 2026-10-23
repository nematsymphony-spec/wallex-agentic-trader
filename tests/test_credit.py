import json
import math
import os
import random
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from wallex_agent.credit_api import CreditError, NoRedirect, WallexCreditClient
from wallex_agent.credit_runner import DEFAULT, Runner, ai_review, settings
from wallex_agent.monthly_policy import relative_score, plan_order


class Server:
    def __init__(self):
        self.calls = []
        self.account = {"clientId": "credit_1", "type": "prop", "isActive": True, "isSuspend": False}
        self.package = {"packageDetail": {"id": 183, "currency": "TMN"}, "userPackageDetail": {
            "status": "open", "connectToWallet": False, "expireAt": "2030-01-01T00:00:00Z"}}
        self.balances = {"TMN": {"total": "5000000", "available": "5000000", "freeze": "0"}}
        self.detail = None
        self.post_error = False

    def __call__(self, method, path, headers, payload):
        self.calls.append((method, path, headers, payload))
        if path == "/sub-accounts": return [self.account]
        if path.endswith("/packages/active"): return self.package
        if path.endswith("/balances-detail"): return self.balances
        if path.endswith("/account/fee"): return {"BTCTMN": {"makerFeeRate": "0.002", "takerFeeRate": "0.003"}}
        if "openOrders" in path: return {}
        if method == "POST":
            if self.post_error: raise CreditError("network lost")
            return {}
        if method == "DELETE": return {}
        if self.detail is None: raise CreditError("unknown/not found")
        return self.detail


class CreditTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.api = WallexCreditClient("test_secret", "credit_1", self.server)
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.db")
        self.runners = []

    def tearDown(self):
        for runner in self.runners:
            runner.close()
        self.temp.cleanup()

    def runner(self, live=False):
        cfg = {**DEFAULT, "trading_end_at": "2029-01-01T00:00:00Z"}
        r = Runner(cfg, self.path, live=live, credit=self.api if live else None)
        self.runners.append(r)
        r.initialize(time.time())
        return r

    def test_both_credit_headers_on_every_request(self):
        self.api.verify(time.time()); self.api.balances()
        for _, _, headers, _ in self.server.calls:
            self.assertEqual(headers, {"X-API-Key": "test_secret", "sub-account-client-id": "credit_1"})
        self.assertNotIn("/v1/account/balances", [c[1] for c in self.server.calls])

    def test_wrong_main_suspended_or_package_account_blocks(self):
        for field, value in (("type", "main"), ("clientId", "wrong"), ("isSuspend", True), ("isActive", False)):
            old = self.server.account[field]; self.server.account[field] = value
            with self.assertRaises(CreditError): self.api.verify(time.time())
            self.server.account[field] = old
        self.server.package["packageDetail"]["id"] = 1
        with self.assertRaises(CreditError): self.api.verify(time.time())

    def test_wallet_link_and_expiry_block(self):
        self.server.package["userPackageDetail"]["connectToWallet"] = True
        with self.assertRaises(CreditError): self.api.verify(time.time())
        self.server.package["userPackageDetail"].update(connectToWallet=False, expireAt="2020-01-01T00:00:00Z")
        with self.assertRaises(CreditError): self.api.verify(time.time())

    def test_no_redirects_and_no_unverified_create(self):
        with self.assertRaises(CreditError): NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test")
        with self.assertRaises(CreditError): self.api.create({"client_id": "ok_1", "type": "LIMIT", "side": "BUY"}, time.time())
        with self.assertRaises(CreditError): self.api.validate_id("bad-id")
        self.assertEqual(len(self.server.calls), 0)

    def test_unknown_post_persists_and_never_reposts(self):
        r = self.runner(True)
        self.server.post_error = True
        order = {"symbol": "BTCTMN", "side": "BUY", "type": "LIMIT", "price": "100", "quantity": "1"}
        with self.assertRaises(CreditError): r.send(order, {"reason": "test"}, time.time())
        self.assertIsNotNone(r.log.load_state("credit_runner")["pending"])
        for _ in range(2):
            with self.assertRaises(CreditError): r.reconcile(time.time())
        self.assertEqual(sum(c[0] == "POST" for c in self.server.calls), 1)
        self.assertNotIn("test_secret", json.dumps(r.log.export_events()))

    def test_partial_buy_cancel_reconciles_wallet_exactly_once(self):
        r = self.runner(True)
        r.send({"symbol": "BTCTMN", "side": "BUY", "type": "LIMIT", "price": "100", "quantity": "10"}, {}, time.time() - 30)
        cid = r.state["pending"]["order"]["client_id"]
        self.server.detail = {"clientOrderId": cid, "symbol": "BTCTMN", "side": "BUY", "active": True, "status": "PARTIALLY_FILLED"}
        self.assertTrue(r.reconcile(time.time()))
        self.assertEqual(self.server.calls[-1][0], "DELETE")
        self.server.detail.update(active=False, status="CANCELED", executedQty="4", executedPrice="100")
        self.server.balances["BTC"] = {"total": "3.988", "available": "3.988", "freeze": "0"}
        self.server.balances["TMN"].update(total="4999600", available="4999600")
        self.assertTrue(r.reconcile(time.time()))
        self.assertEqual(r.state["position"]["qty"], "3.988")
        self.assertFalse(r.reconcile(time.time()))
        self.assertEqual(r.state["cash"], "4999600")

    def test_restart_binding_and_external_wallet_change_block(self):
        r = self.runner(True)
        self.server.balances["TMN"]["available"] = "4900000"
        with self.assertRaises(CreditError): r.wallet()
        other = {**r.cfg, "profit_goal": "0.05"}
        r.lock.close()
        with self.assertRaises(CreditError): Runner(other, self.path, live=True, credit=self.api)

    def test_unmanaged_assets_cannot_be_adopted(self):
        self.server.balances["BTC"] = {"total": "1", "available": "1", "freeze": "0"}
        r = Runner(DEFAULT, self.path, credit=self.api, live=False)
        self.runners.append(r)
        r.live = True
        with self.assertRaises(CreditError): r.initialize(time.time())

    def test_shared_relative_score_matches_old_formula(self):
        rng = random.Random(42)
        for _ in range(500):
            coin = {"r24": rng.uniform(-.1, .2), "r72": rng.uniform(-.2, .3), "rv24": rng.random() / 10, "rv72": rng.random() / 10}
            fx = {"r24": .002, "r72": .01}; btc = {"r24": .01, "r72": .02}
            for s in ("BTCTMN", "PUMPTMN"):
                ref = fx if s == "BTCTMN" else btc
                old = (coin["r24"] - ref["r24"]) / max(.001, coin["rv24"] * math.sqrt(24))
                expected = old if coin["r24"] > max(0, fx["r24"]) and coin["r72"] > fx["r72"] and old > 0 else None
                self.assertEqual(relative_score(s, coin, fx, btc), expected)

    def test_ai_missing_invalid_and_veto(self):
        snapshot = {"candidates": [{"symbol": "BTCTMN"}], "hour": 1}
        cfg = {**DEFAULT, "ai_review": True}
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}): self.assertEqual(ai_review(snapshot, {}, cfg)["action"], "HOLD")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}), patch("wallex_agent.credit_runner.request_json", return_value={"status": "completed", "output": []}):
            self.assertEqual(ai_review(snapshot, {}, cfg)["action"], "HOLD")
        response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"action":"HOLD","reason":"Costs outweigh strength"}'}]}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}), patch("wallex_agent.credit_runner.request_json", return_value=response):
            self.assertEqual(ai_review(snapshot, {}, cfg)["reason"], "Costs outweigh strength")

    def test_order_costs_fee_reserve_and_depth(self):
        meta = {"symbol": "BTCTMN", "tickSize": 0, "stepSize": 1, "minQty": ".1", "minNotional": "1"}
        book = {"ask": [{"price": "100", "quantity": "1000000"}], "bid": [{"price": "99.9", "quantity": "1000000"}]}
        order, costs = plan_order(meta, book, "BUY", "5000000", 0, DEFAULT)
        from decimal import Decimal as D
        self.assertLessEqual(D(order["price"]) * D(order["quantity"]) * D("1.003"), D("5000000"))
        self.assertGreater(D(costs["instant_roundtrip_cost"]), D("0.006"))
        bad = deepcopy(book); bad["bid"][0]["quantity"] = "1"
        with self.assertRaises(ValueError): plan_order(meta, bad, "BUY", "5000000", 0, DEFAULT)
        bad = deepcopy(book); bad["ask"][0]["quantity"] = "1"
        self.assertEqual(plan_order(meta, bad, "SELL", 0, "100", DEFAULT)[0]["side"], "SELL")
        bad = deepcopy(book); bad["bid"][0]["quantity"] = "10"
        self.assertEqual(plan_order(meta, bad, "SELL", 0, "100", DEFAULT)[0]["quantity"], "10")

    def test_two_processes_cannot_own_same_state(self):
        r = self.runner()
        with self.assertRaises(CreditError): Runner(DEFAULT, self.path)
        r.lock.close()

    def test_actual_fee_required_and_upper_bound_enforced(self):
        r = self.runner(True)
        self.assertEqual(r.entry_config("BTCTMN")["fee_rate"], "0.003")
        with self.assertRaises(CreditError): r.entry_config("PUMPTMN")
        r.cfg["fee_rate"] = "0.001"
        with self.assertRaises(CreditError): r.entry_config("BTCTMN")

    def test_paper_entry_protective_exit_and_restart(self):
        class Public:
            price = "100"
            def depth(self, symbol):
                return {"ask": [{"price": self.price, "quantity": "1000000"}], "bid": [{"price": self.price, "quantity": "1000000"}]}
            def markets(self):
                return {"BTCTMN": {"symbol": "BTCTMN", "spot_active": True, "quoteAsset": "TMN", "tickSize": 0, "stepSize": 1, "minQty": ".1", "minNotional": "1"}}
            def unavailable(self, _): return set()
        r = self.runner()
        r.api = Public()
        snapshot = {"candidates": [{"symbol": "BTCTMN"}], "hour": int(time.time()) // 3600 * 3600, "missing_references": []}
        with patch("wallex_agent.credit_runner.collect", return_value=snapshot):
            self.assertEqual(r.tick()["action"], "BUY")
        self.assertIsNotNone(r.state["position"])
        # Force a real restart of the journal, preserving entry quantity/price.
        cfg, public = r.cfg, r.api
        r.close(); self.runners.remove(r)
        r = Runner(cfg, self.path, api=public); self.runners.append(r)
        r.cfg["ai_review"] = True
        public.price = "90"
        with patch("wallex_agent.credit_runner.ai_review", side_effect=AssertionError("exit must not ask AI")):
            result = r.tick()
        self.assertEqual(result["reason"], "price_stop")
        self.assertIsNone(r.state["position"])
        self.assertLess(float(r.state["cash"]), 5000000)
        self.assertTrue(r.state["daily_pause"])
        self.assertTrue(r.log.verify())

    def test_closeout_takes_priority_and_remainder_keeps_exit_reason(self):
        r = self.runner()
        r.cfg["trading_end_at"] = "2020-01-01T00:00:00Z"
        r.state["position"] = {"symbol": "BTCTMN", "qty": "100", "entry": "100", "entry_at": time.time(), "entry_event": "fixture"}
        class Thin:
            def markets(self): return {"BTCTMN": {"symbol": "BTCTMN", "spot_active": True, "tickSize": 0, "stepSize": 1, "minQty": ".1", "minNotional": "1"}}
            def unavailable(self, _): return set()
            def depth(self, symbol): return {"ask": [{"price": "100", "quantity": "1000"}], "bid": [{"price": "100", "quantity": "10"}]}
        r.api = Thin()
        result = r.tick()
        self.assertEqual(result["reason"], "competition_closeout")
        self.assertEqual(r.state["position"]["qty"], "90")
        self.assertEqual(r.state["position"]["exit_reason"], "competition_closeout")
        self.assertEqual(r.tick()["action"], "SELL")


if __name__ == "__main__": unittest.main()
