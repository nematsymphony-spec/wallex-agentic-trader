"""Credit-only Wallex adapter. No main-wallet, transfer or withdrawal methods."""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone


class CreditError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CreditError("Authenticated redirects are forbidden")


def transport(method, path, headers, payload=None):
    if not path.startswith(("/v1/account/", "/v1/prop/user/", "/sub-accounts")):
        raise CreditError("Unsupported credit endpoint")
    req = urllib.request.Request("https://api.wallex.ir" + path,
        data=None if payload is None else json.dumps(payload).encode(),
        method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as response:
            data = json.load(response)
    except Exception as exc:
        # Do not expose server responses, headers or key through exceptions.
        raise CreditError("Credit request failed: " + method + " " + path.split("?")[0]) from None
    if not isinstance(data, dict) or data.get("success") is False or "result" not in data:
        raise CreditError("Credit API rejected response")
    return data["result"]


class WallexCreditClient:
    def __init__(self, key=None, client_id=None, send=transport):
        self.key = key or os.environ.get("WALLEX_API_KEY", "")
        self.client_id = client_id or os.environ.get("WALLEX_SUBACCOUNT_CLIENT_ID", "")
        if not self.key or not self.client_id or any(c in self.key + self.client_id for c in "\r\n"):
            raise CreditError("Set WALLEX_API_KEY and WALLEX_SUBACCOUNT_CLIENT_ID securely")
        self.send = send
        self.verified_until = 0

    def request(self, method, path, payload=None):
        return self.send(method, path, {"X-API-Key": self.key,
                         "sub-account-client-id": self.client_id}, payload)

    def verify(self, now, package_id=183):
        self.verified_until = 0
        accounts = self.request("GET", "/sub-accounts")
        if not isinstance(accounts, list):
            raise CreditError("Credit account list unavailable")
        matches = [a for a in accounts if a.get("clientId") == self.client_id]
        if len(matches) != 1 or matches[0].get("type") != "prop" or matches[0].get("isActive") is not True or matches[0].get("isSuspend") is not False:
            raise CreditError("Expected active prop account not verified")
        active = self.request("GET", "/v1/prop/user/packages/active")
        package, user = active.get("packageDetail", {}), active.get("userPackageDetail", {})
        if package.get("id") != package_id or package.get("currency") != "TMN":
            raise CreditError("Wrong competition package or currency")
        if str(user.get("status", "")).lower() not in ("active", "open"):
            raise CreditError("Credit package is not active/open")
        if user.get("connectToWallet") not in (None, False):
            raise CreditError("Main wallet connection must be disabled")
        try:
            expiry = datetime.fromisoformat(user["expireAt"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError):
            raise CreditError("Credit expiry unavailable") from None
        if expiry <= now:
            raise CreditError("Credit account expired")
        self.verified_until = min(now + 45, expiry)
        return {"package_id": package_id, "expires_at": expiry, "account_type": "prop",
                "wallet_connected": False, "status": user["status"]}

    def balances(self):
        result = self.request("GET", "/v1/account/balances-detail")
        if not isinstance(result, dict) or "TMN" not in result:
            raise CreditError("Credit wallet unavailable")
        return result

    def fees(self):
        result = self.request("GET", "/v1/account/fee")
        if not isinstance(result, dict):
            raise CreditError("Account fee rates unavailable")
        return result

    def open_orders(self):
        result = self.request("GET", "/v1/account/openOrders?per_page=100&page=1")
        # A nonempty unknown shape is not treated as an empty account.
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            orders = []
            for value in result.values():
                if not isinstance(value, list):
                    raise CreditError("Unknown open-order response")
                orders.extend(value)
            return orders
        raise CreditError("Unknown open-order response")

    @staticmethod
    def validate_id(client_id):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", client_id):
            raise CreditError("Invalid order client ID")

    def order(self, client_id):
        self.validate_id(client_id)
        return self.request("GET", "/v1/account/orders/" + client_id)

    def cancel(self, client_id):
        self.validate_id(client_id)
        return self.request("DELETE", "/v1/account/orders/" + client_id)

    def create(self, order, now):
        if now >= self.verified_until:
            raise CreditError("Credit verification expired; no order sent")
        self.validate_id(order["client_id"])
        if order.get("type") != "LIMIT" or order.get("side") not in ("BUY", "SELL"):
            raise CreditError("Only bounded LIMIT spot orders supported")
        return self.request("POST", "/v1/account/orders", order)
