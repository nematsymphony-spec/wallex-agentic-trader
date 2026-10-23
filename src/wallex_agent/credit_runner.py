"""Durable single-position runner. Same policy in public-book paper and live mode.

Live positions are reconciled from the dedicated wallet, never fabricated fills.
An ambiguous request leaves a durable pending order and cannot trigger a retry.
"""
import argparse
import fcntl
import getpass
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from .clients import WallexPublic, request_json
from .credit_api import WallexCreditClient, CreditError
from .engine import AuditLog
from .monthly_policy import collect, decimal, plan_order

DEFAULT = {"package_id": 183, "allocation": "1", "profit_goal": "0.03",
           "fee_rate": "0.003", "position_stop": "0.05",
           "daily_buy_pause": "0.02", "max_hold_hours": 48,
           "limit_slippage_bps": 20, "max_roundtrip_cost": "0.015",
           "order_ttl_seconds": 20, "poll_seconds": 5, "initial_tmn": "5000000",
           "ai_review": False, "openai_model": "gpt-4.1-mini",
           "trading_end_at": None, "closeout_buffer_seconds": 300}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed.timestamp()


def settings(path):
    raw = json.loads(Path(path).read_text())
    unknown = set(raw) - set(DEFAULT)
    if unknown:
        raise ValueError("Unknown configuration fields: " + ",".join(sorted(unknown)))
    cfg = {**DEFAULT, **raw}
    if cfg["package_id"] != 183 or not 0 < decimal(cfg["allocation"]) <= 1:
        raise ValueError("Expected package 183 and allocation in (0,1]")
    for key in ("profit_goal", "position_stop", "daily_buy_pause", "max_roundtrip_cost"):
        if not 0 < decimal(cfg[key]) < 1:
            raise ValueError("Invalid risk setting: " + key)
    if not 0 <= decimal(cfg["fee_rate"]) < D("0.02"):
        raise ValueError("Invalid fee rate")
    if not 1 <= cfg["poll_seconds"] <= 30 or not 1 <= cfg["order_ttl_seconds"] <= 60 or not 0 < cfg["max_hold_hours"] <= 48:
        raise ValueError("Invalid monitoring interval")
    if not 0 <= cfg["limit_slippage_bps"] <= 100 or not 60 <= cfg["closeout_buffer_seconds"] <= 3600:
        raise ValueError("Invalid execution bounds")
    if not isinstance(cfg["ai_review"], bool):
        raise ValueError("Boolean settings required")
    if cfg["trading_end_at"]:
        timestamp(cfg["trading_end_at"])
    return cfg


def ai_review(snapshot, execution, cfg):
    candidate = snapshot["candidates"][0]
    inputs = {"candidate": candidate, "other_candidates": snapshot["candidates"][1:],
              "execution": execution, "completed_hour": snapshot["hour"],
              "rule": "Relative strength hourly, 5% price stop, max48h, monthly goal"}
    if not cfg["ai_review"]:
        return {"action": "BUY", "reason": "Deterministic research policy; AI disabled", "input": inputs, "model": "none"}
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"action": "HOLD", "reason": "OPENAI_API_KEY missing", "input": inputs}
    schema = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["BUY", "HOLD"]},
        "reason": {"type": "string"}}, "required": ["action", "reason"], "additionalProperties": False}
    try:
        response = request_json("https://api.openai.com/v1/responses", method="POST",
            headers={"Authorization": "Bearer " + key}, timeout=25, payload={
                "model": cfg["openai_model"], "store": False,
                "instructions": "Review this rule-selected spot entry. You have genuine veto authority. BUY only if supplied data support strength beyond measured costs; otherwise HOLD. No external facts, invented forecasts or alternative symbols. Explain using supplied numeric evidence.",
                "input": json.dumps(inputs), "max_output_tokens": 400,
                "text": {"format": {"type": "json_schema", "name": "entry_review", "schema": schema, "strict": True}}})
        texts = [c["text"] for item in response.get("output", []) if item.get("type") == "message" for c in item.get("content", []) if c.get("type") == "output_text"]
        if response.get("status") != "completed" or len(texts) != 1:
            raise ValueError("Incomplete AI response")
        result = json.loads(texts[0])
        if set(result) != {"action", "reason"} or result["action"] not in ("BUY", "HOLD") or not isinstance(result["reason"], str) or not result["reason"].strip():
            raise ValueError("Invalid AI decision")
        return {**result, "input": inputs, "model": cfg["openai_model"]}
    except Exception:
        return {"action": "HOLD", "reason": "AI unavailable or invalid", "input": inputs, "model": cfg["openai_model"]}


class Runner:
    def __init__(self, cfg, path, live=False, api=None, credit=None):
        self.cfg, self.live = cfg, live
        self.api, self.credit = api or WallexPublic(), credit
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise CreditError("Another runner owns this database") from None
        self.log = AuditLog(str(path))
        os.chmod(path, 0o600)
        if not self.log.verify():
            raise CreditError("Audit chain invalid")
        self.binding = hashlib.sha256(json.dumps({"cfg": cfg, "live": live,
            "account": credit.client_id if credit else "paper"}, sort_keys=True).encode()).hexdigest()
        self.state = self.log.load_state("credit_runner")
        if self.state and self.state["binding"] != self.binding:
            self.lock.close()
            raise CreditError("Config/account changed; use a separate state DB, never abandon a live position")
        if live and not cfg["trading_end_at"]:
            self.lock.close()
            raise CreditError("Live requires explicit competition trading_end_at")
        self.last_report = {}

    def close(self):
        self.log.connection.close()
        self.lock.close()

    def persist(self):
        self.log.save_state("credit_runner", self.state)

    def event(self, kind, payload, symbol=None):
        return self.log.append(kind, symbol, payload)

    def wallet(self):
        balances = self.credit.balances()
        # Frozen funds mean an outstanding/unmanaged order. Pending orders are
        # reconciled before this guard, so never spend already reserved money.
        if any(decimal(b.get("freeze", 0)) > 0 for b in balances.values()):
            raise CreditError("Frozen funds remain; cannot start a new operation")
        holdings = {s: decimal(b["total"]) for s, b in balances.items() if s != "TMN" and decimal(b["total"]) > 0}
        expected = self.state.get("position") if self.state else None
        if holdings and (not expected or set(holdings) != {expected["symbol"][:-3]}):
            raise CreditError("Unmanaged wallet assets; no automatic adoption")
        cash = decimal(balances["TMN"]["available"])
        if self.state:
            if abs(cash - decimal(self.state["cash"])) > D("1"):
                raise CreditError("Wallet cash differs from journal; reconcile externally changed account")
            if expected and decimal(expected["qty"]) != holdings.get(expected["symbol"][:-3], D(0)):
                raise CreditError("Wallet asset quantity differs from journal")
        return cash

    def initialize(self, now):
        cash = self.wallet() if self.live else decimal(self.cfg["initial_tmn"])
        if cash <= 0:
            raise CreditError("No available TMN capital")
        self.state = {"binding": self.binding, "cash": str(cash), "position": None,
                      "pending": None, "block_start": now, "block_equity": str(cash),
                      "goal_hit": False, "day": None, "day_equity": str(cash),
                      "daily_pause": False, "last_hour": None, "last_exit_hour": None}
        self.persist()
        self.event("credit_start", {"mode": "live" if self.live else "paper", "config": self.cfg, "initial_tmn": str(cash)})

    def reconcile(self, now):
        pending = self.state["pending"]
        if not pending:
            return False
        order = self.credit.order(pending["order"]["client_id"])
        # Unknown/not-found/rejected requests are never re-POSTed automatically.
        if order.get("clientOrderId") != pending["order"]["client_id"] or order.get("symbol") != pending["order"]["symbol"] or order.get("side") != pending["order"]["side"]:
            raise CreditError("Order identity mismatch")
        status = str(order.get("status", "")).upper()
        terminal = status in ("FINISHED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED") and order.get("active") is False
        if not terminal:
            if now - pending["created_at"] >= self.cfg["order_ttl_seconds"]:
                self.credit.cancel(pending["order"]["client_id"])
                self.event("cancel_requested", {"client_id": pending["order"]["client_id"]})
            return True
        balances = self.credit.balances()
        asset = pending["order"]["symbol"][:-3]
        qty = decimal(balances.get(asset, {}).get("total", 0))
        before_qty = decimal(pending["before_qty"])
        filled = decimal(order.get("executedQty", 0))
        if any(decimal(b.get("freeze", 0)) for b in balances.values()):
            raise CreditError("Wallet still frozen after terminal order")
        if pending["order"]["side"] == "BUY":
            if filled > 0:
                price = decimal(order.get("executedPrice", 0))
                if price <= 0 or qty <= 0 or before_qty != 0 or qty > filled:
                    raise CreditError("Cannot reconcile executed buy and wallet")
                self.state["position"] = {"symbol": pending["order"]["symbol"], "qty": str(qty), "entry": str(price), "entry_at": pending["created_at"], "entry_event": pending["decision_event"]}
            elif qty != before_qty:
                raise CreditError("Unexplained asset change")
        else:
            if qty > before_qty or before_qty - qty > filled * D("1.02"):
                raise CreditError("Cannot reconcile executed sell and wallet")
            if qty == 0:
                self.state["position"] = None
                self.state["last_exit_hour"] = int(now) // 3600
            else:
                self.state["position"]["qty"] = str(qty)
        self.state["cash"] = str(decimal(balances["TMN"]["available"]))
        # Position, balances and pending completion share ONE SQLite state write.
        self.state["pending"] = None
        self.persist()
        self.event("exchange_terminal", {"client_id": pending["order"]["client_id"], "status": status,
            "executed_qty": str(filled), "executed_price": order.get("executedPrice"),
            "reported_fee": order.get("fee"), "wallet_cash": self.state["cash"], "position": self.state["position"]}, pending["order"]["symbol"])
        return True

    def send(self, order, evidence, now):
        if self.state["pending"]:
            raise CreditError("Pending order must be reconciled before another order")
        order = {**order, "client_id": "wc_" + uuid.uuid4().hex}
        decision_hash = self.event("order_intent", {"order": order, "evidence": evidence}, order["symbol"])
        if self.live:
            self.credit.verify(now)
            if self.credit.open_orders():
                raise CreditError("Unmanaged/active exchange orders block new orders")
            self.wallet()
            self.state["pending"] = {"order": order, "created_at": now, "decision_event": decision_hash,
                "before_qty": self.state["position"]["qty"] if self.state["position"] else "0"}
            self.persist()  # before network write, including crash-before-send case
            try:
                self.credit.create(order, time.time())
            except Exception:
                self.event("order_unknown", {"client_id": order["client_id"], "action": "reconcile only; do not retry POST"})
                raise CreditError("Order result unknown; pending ID retained") from None
            return
        # Paper consumes the captured public book; queue/latency are NOT tested.
        fee = decimal(self.cfg["fee_rate"])
        qty, price = decimal(order["quantity"]), decimal(order["price"])
        cash = decimal(self.state["cash"])
        if order["side"] == "BUY":
            # Conservative bounded limit price, not an asserted exchange fill.
            self.state["cash"] = str(cash - qty * price * (1 + fee))
            self.state["position"] = {"symbol": order["symbol"], "qty": str(qty), "entry": str(price), "entry_at": now, "entry_event": decision_hash}
        else:
            self.state["cash"] = str(cash + qty * price * (1 - fee))
            remaining = decimal(self.state["position"]["qty"]) - qty
            if remaining > 0:
                self.state["position"]["qty"] = str(remaining)
            else:
                self.state["position"] = None
                self.state["last_exit_hour"] = int(now) // 3600
        self.persist()
        self.event("paper_fill", {"order": order, "fee_rate_scenario": str(fee), "assumption": "immediate bounded-limit fill; no queue/latency evidence"}, order["symbol"])

    def entry_config(self, symbol):
        if not self.live:
            return self.cfg
        rates = self.credit.fees().get(symbol)
        if not isinstance(rates, dict):
            raise CreditError("Actual account fees unavailable for selected market")
        actual = max(decimal(rates["makerFeeRate"]), decimal(rates["takerFeeRate"]))
        if actual >= D("0.02") or actual > decimal(self.cfg["fee_rate"]):
            raise CreditError("Actual fee exceeds configured conservative reserve; review config and freeze again")
        self.event("account_fees", {"symbol": symbol, "maker": rates["makerFeeRate"], "taker": rates["takerFeeRate"], "planning_rate": str(actual)})
        return {**self.cfg, "fee_rate": str(actual)}

    def tick(self, now=None):
        now = now or time.time()
        if self.live:
            verified = self.credit.verify(now)
            if timestamp(self.cfg["trading_end_at"]) > verified["expires_at"]:
                raise CreditError("Trading end exceeds credit expiry")
        if self.state is None:
            self.initialize(now)
        if self.live and self.reconcile(now):
            return {"action": "RECONCILE", "pending": bool(self.state["pending"])}
        if self.live:
            if self.credit.open_orders():
                raise CreditError("Unknown exchange open orders")
            self.wallet()
        pos = self.state["position"]
        cash, fee = decimal(self.state["cash"]), decimal(self.cfg["fee_rate"])
        book, bid = (None, D(0))
        if pos:
            book = self.api.depth(pos["symbol"])
            bid = max(decimal(l["price"]) for l in book["bid"])
        equity = cash + (decimal(pos["qty"]) * bid * (1 - fee) if pos else 0)
        if now - self.state["block_start"] >= 720 * 3600:
            blocks = int((now - self.state["block_start"]) // (720 * 3600))
            self.state.update(block_start=self.state["block_start"] + blocks * 720 * 3600,
                              block_equity=str(equity), goal_hit=False)
            self.event("monthly_reset", {"reference_tmn": str(equity), "missed_boundaries": blocks - 1})
        day = datetime.fromtimestamp(now, ZoneInfo("Asia/Tehran")).date().isoformat()
        if day != self.state["day"]:
            self.state.update(day=day, day_equity=str(equity), daily_pause=False)
        if equity <= decimal(self.state["day_equity"]) * (1 - decimal(self.cfg["daily_buy_pause"])):
            self.state["daily_pause"] = True
        goal = decimal(self.state["block_equity"]) * (1 + decimal(self.cfg["profit_goal"]))
        deadline = bool(self.cfg["trading_end_at"]) and now >= timestamp(self.cfg["trading_end_at"]) - self.cfg["closeout_buffer_seconds"]
        if pos:
            reason = pos.get("exit_reason") or ("competition_closeout" if deadline else "price_stop" if bid <= decimal(pos["entry"]) * (1 - decimal(self.cfg["position_stop"])) else "max_48h" if now - pos["entry_at"] >= self.cfg["max_hold_hours"] * 3600 else "monthly_goal" if equity >= goal else None)
            if reason:
                pos["exit_reason"] = reason
            self.persist()
            if reason:
                # Current exchange permission checked on exits as well; inability
                # to exit is visible, never reported as a successful closeout.
                meta = self.api.markets()[pos["symbol"]]
                if pos["symbol"] in self.api.unavailable(183) or not meta.get("spot_active"):
                    raise CreditError("Held market unavailable; exit requires exchange assistance")
                fresh = self.api.depth(pos["symbol"])
                order, costs = plan_order(meta, fresh, "SELL", cash, pos["qty"], self.cfg)
                self.send(order, {"reason": reason, "costs": costs, "equity_tmn": str(equity), "depth": fresh, "market_metadata": meta, "entry_event": pos.get("entry_event"), "depth_received_at": time.time()}, now)
                return {"action": "SELL", "reason": reason, "symbol": pos["symbol"]}
            return {"action": "HOLD", "reason": "position monitored", "equity_tmn": str(equity)}
        if cash >= goal:
            self.state["goal_hit"] = True
        self.persist()
        if deadline or self.state["goal_hit"] or self.state["daily_pause"]:
            return {"action": "HOLD", "reason": "deadline/goal/daily pause", "cash_tmn": str(cash)}
        hour = int(now) // 3600
        if self.state["last_hour"] == hour or self.state["last_exit_hour"] == hour:
            return {"action": "HOLD", "reason": "wait next completed hour"}
        # Reserve this decision hour before fetching; restart cannot duplicate it.
        self.state["last_hour"] = hour
        self.persist()
        snapshot = collect(self.api, now)
        snapshot_hash = self.event("market_snapshot", snapshot)
        if not snapshot["candidates"]:
            return {"action": "HOLD", "reason": "reference data incomplete" if snapshot["missing_references"] else "no eligible relative strength", "missing_references": snapshot["missing_references"], "snapshot": snapshot_hash}
        symbol = snapshot["candidates"][0]["symbol"]
        if int(time.time()) // 3600 != hour:
            raise CreditError("Snapshot crossed hour boundary; defer decision")
        meta = self.api.markets()[symbol]
        if symbol in self.api.unavailable(183) or not meta.get("spot_active") or meta.get("quoteAsset") != "TMN":
            raise CreditError("Market no longer eligible")
        entry_cfg = self.entry_config(symbol)
        order, costs = plan_order(meta, self.api.depth(symbol), "BUY", cash, 0, entry_cfg)
        review = ai_review(snapshot, costs, self.cfg)
        event = self.event("entry_decision", {**review, "snapshot_event": snapshot_hash}, symbol)
        if review["action"] != "BUY":
            return {"action": "HOLD", "reason": review["reason"], "symbol": symbol}
        if time.time() - now > 180 or int(time.time()) // 3600 != hour:
            raise CreditError("Decision stale; no order")
        # AI latency cannot make the old book executable; recalculate bounded plan.
        meta = self.api.markets()[symbol]
        if symbol in self.api.unavailable(183) or not meta.get("spot_active"):
            raise CreditError("Market permission changed")
        final_book = self.api.depth(symbol)
        order, fresh_costs = plan_order(meta, final_book, "BUY", cash, 0, entry_cfg)
        if deadline or (self.cfg["trading_end_at"] and time.time() >= timestamp(self.cfg["trading_end_at"]) - self.cfg["closeout_buffer_seconds"]):
            raise CreditError("Closeout window started during scan")
        self.send(order, {"decision_event": event, "snapshot_event": snapshot_hash, "costs": fresh_costs,
            "depth": final_book, "market_metadata": meta, "depth_received_at": time.time()}, time.time())
        return {"action": "BUY", "symbol": symbol, "decision_event": event, "mode": "live" if self.live else "paper"}


def load_secret_file(path):
    if not path:
        return
    p = Path(path)
    if p.stat().st_mode & 0o077:
        raise CreditError("Secrets file must have mode 0600")
    values = json.loads(p.read_text())
    for name in ("WALLEX_API_KEY", "WALLEX_SUBACCOUNT_CLIENT_ID", "OPENAI_API_KEY"):
        if values.get(name):
            os.environ[name] = values[name]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wallex-credit")
    parser.add_argument("command", choices=["setup", "doctor", "paper", "run", "status", "export", "freeze"])
    parser.add_argument("--config", default="config/credit-research.json")
    parser.add_argument("--secrets", help="Optional local 0600 JSON file; never pass keys as CLI arguments")
    parser.add_argument("--db", default="results/private/credit-paper.db")
    parser.add_argument("--cycles", type=int, default=None, help="Paper default1; live always continuous (0)")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            dest = Path(args.secrets or ".secrets/credit.json")
            if dest.exists():
                raise CreditError("Secrets file already exists; refusing to overwrite")
            values = {"WALLEX_API_KEY": getpass.getpass("Credit account API key (hidden): "),
                      "WALLEX_SUBACCOUNT_CLIENT_ID": getpass.getpass("Credit sub-account client ID (hidden): ")}
            if not all(values.values()):
                raise CreditError("Both credit credentials are required")
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(values, f)
            print("Stored locally with mode0600. Run doctor; no order sent.")
            return 0
        if args.command == "freeze":
            from .release import freeze
            print(json.dumps(freeze(), indent=2))
            return 0
        if args.command in ("status", "export"):
            log = AuditLog(args.db)
            if not log.verify():
                raise CreditError("Audit chain invalid")
            print(json.dumps(log.load_state("credit_runner") if args.command == "status" else log.export_events(), ensure_ascii=False, indent=2))
            return 0
        load_secret_file(args.secrets)
        cfg = settings(args.config)
        live = args.command in ("doctor", "run")
        credit = WallexCreditClient() if live else None
        if args.command == "doctor":
            result = credit.verify(time.time())
            balances = credit.balances()
            fees = credit.fees()
            print(json.dumps({**result, "available_tmn": balances["TMN"]["available"],
                "open_orders": len(credit.open_orders()), "configured_fee_reserve": cfg["fee_rate"],
                "account_fee_market_count": len(fees), "example_rates": {s: fees[s] for s in ("BTCTMN", "PUMPTMN") if s in fees},
                "trading_end_configured": bool(cfg["trading_end_at"]), "order_sent": False}, indent=2))
            return 0
        if args.command == "run" and args.db == "results/private/credit-paper.db":
            raise CreditError("Use a dedicated --db results/private/credit-live.db")
        if args.command == "run":
            from .release import verify_release
            verify_release()
        if args.cycles is None:
            args.cycles = 0 if live else 1
        if live and args.cycles != 0:
            raise CreditError("Live must monitor continuously; --cycles must be 0")
        if args.cycles < 0:
            raise ValueError("cycles must be >=0")
        runner = Runner(cfg, args.db, live=live, credit=credit)
        count = 0
        failed = False
        while args.cycles == 0 or count < args.cycles:
            try:
                report = runner.tick()
                failed = False
                runner.event("cycle", report)
                print(json.dumps(report, ensure_ascii=False), flush=True)
            except Exception as exc:
                failed = True
                # Errors do not create orders or remove pending state. Continue
                # monitoring after temporary API faults, retaining protective exits.
                report = {"action": "HOLD", "error_type": type(exc).__name__, "reason": str(exc) if isinstance(exc, (CreditError, ValueError)) else "API/data failure"}
                runner.event("cycle_error", report)
                print(json.dumps(report), flush=True)
            count += 1
            if args.cycles == 0 or count < args.cycles:
                time.sleep(cfg["poll_seconds"])
        runner.close()
        return 2 if failed else 0
    except (CreditError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc), "order_sent_by_this_command": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
