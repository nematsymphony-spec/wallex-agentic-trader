from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from .clients import WallexPublic
from .core import RiskSettings
from .engine import AuditLog, collect_live_views, run_backtest, run_shadow
from .selector import rank_views


def epoch(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("Use timezone-aware ISO date, e.g. 2026-10-18T00:00:00Z")
    return int(dt.timestamp())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wallex-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    markets = sub.add_parser("markets", help="Check live spot status and credit package exclusion list")
    markets.add_argument("--package-id", type=int, default=183)
    scan = sub.add_parser("scan", help="Rank all eligible TMN spot markets without AI or orders")
    scan.add_argument("--package-id", type=int, default=183)
    scan.add_argument("--top", type=int, default=10)
    backtest = sub.add_parser("backtest", help="Run historical simulation on completed hourly bars")
    backtest.add_argument("--start", type=epoch, required=True)
    backtest.add_argument("--end", type=epoch, required=True)
    backtest.add_argument("--model", choices=["rule", "openai"], default="rule")
    backtest.add_argument("--spread-bps", type=Decimal, default=Decimal(20))
    backtest.add_argument("--package-id", type=int, default=183)
    backtest.add_argument("--db", default="results/backtest.db")
    shadow = sub.add_parser("shadow", help="Observe live markets, no exchange write")
    shadow.add_argument("--model", choices=["rule", "openai"], default="openai")
    shadow.add_argument("--package-id", type=int, default=183)
    shadow.add_argument("--db", default="results/shadow.db")
    shadow.add_argument("--cycles", type=int, default=1)
    shadow.add_argument("--forever", action="store_true")
    shadow.add_argument("--interval", type=int, default=3600)
    live = sub.add_parser("live", help="Fail-closed until credit-account order routing is proven")
    verify = sub.add_parser("verify-log", help="Verify hash chain of audit events")
    verify.add_argument("db")
    export = sub.add_parser("export-log", help="Print audit events as JSON")
    export.add_argument("db")
    export.add_argument("--last", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "markets":
            api = WallexPublic()
            info, unavailable = api.markets(), api.unavailable(args.package_id)
            output = {"package_id": args.package_id, "tmn": sorted(s for s, v in info.items() if v.get("quoteAsset") == "TMN" and v.get("spot_active") and s not in unavailable), "usdt": sorted(s for s, v in info.items() if v.get("quoteAsset") == "USDT" and v.get("spot_active") and s not in unavailable)}
            output["counts"] = {"tmn": len(output["tmn"]), "usdt": len(output["usdt"])}
            print(json.dumps(output, indent=2, ensure_ascii=False))
        elif args.command == "scan":
            views, catalog = collect_live_views(WallexPublic(), args.package_id)
            ranked, reasons = rank_views(views, RiskSettings(), catalog["last_complete_hour"])
            counts = {reason: list(reasons.values()).count(reason) for reason in sorted(set(reasons.values()))}
            print(json.dumps({"last_complete_hour": catalog["last_complete_hour"], "allowed_tmn": len(catalog["allowed_tmn"]), "allowed_usdt_cataloged": len(catalog["allowed_usdt"]), "scanned": len(views), "fetch_errors": catalog["errors"], "gate_counts": counts, "top": ranked[:max(args.top, 0)]}, indent=2, ensure_ascii=False, default=str))
        elif args.command == "backtest":
            print(json.dumps(run_backtest(args.db, args.start, args.end, args.model, args.spread_bps, args.package_id), indent=2, ensure_ascii=False))
        elif args.command == "shadow":
            if args.cycles < 1 or args.interval < 60:
                parser.error("cycles must be positive; interval must be >=60 seconds")
            i = 0
            while args.forever or i < args.cycles:
                print(json.dumps(run_shadow(args.db, args.package_id, args.model), ensure_ascii=False), flush=True)
                i += 1
                if args.forever or i < args.cycles:
                    time.sleep(args.interval)
        elif args.command == "live":
            print("LIVE DISABLED: official spot order endpoint does not document credit subaccount routing. No order was sent.", file=sys.stderr)
            return 3
        elif args.command == "verify-log":
            good = AuditLog(args.db).verify()
            print("valid" if good else "invalid")
            return 0 if good else 2
        elif args.command == "export-log":
            log = AuditLog(args.db)
            if not log.verify():
                raise ValueError("audit chain invalid")
            print(json.dumps(log.export_events(args.last), indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
