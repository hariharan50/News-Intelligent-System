"""Razorpay REST API (subscriptions, payments, invoices) and signature checks.

Kept to the handful of calls we need, on our own httpx client, so tests can swap the
transport. Docs: https://razorpay.com/docs/api/payments/subscriptions/
"""

import hashlib
import hmac
import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

# Monthly plans: Razorpay needs a total number of cycles. 120 = ten years, i.e. "until
# cancelled" in practice.
TOTAL_CYCLES = 120


class RazorpayError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _equal(expected: str, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(expected, given)


def verify_checkout_signature(payment_id: str, subscription_id: str, signature: str) -> bool:
    """The signature Checkout returns after a subscription payment:
    HMAC-SHA256(payment_id + "|" + subscription_id, key_secret)."""
    secret = get_settings().razorpay_key_secret or ""
    message = f"{payment_id}|{subscription_id}".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return _equal(expected, signature)


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    """X-Razorpay-Signature: HMAC-SHA256 of the raw request body with the webhook secret."""
    secret = get_settings().razorpay_webhook_secret
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return _equal(expected, signature)


class RazorpayClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self.base = s.razorpay_api_base.rstrip("/")
        self.auth = (s.razorpay_key_id or "", s.razorpay_key_secret or "")
        self.http = http or httpx.AsyncClient(timeout=20.0)

    async def _call(self, method: str, path: str, json: dict | None = None) -> dict:
        try:
            resp = await self.http.request(method, f"{self.base}{path}", json=json, auth=self.auth)
        except httpx.HTTPError as exc:
            log.warning("Razorpay %s %s failed: %s", method, path, exc)
            raise RazorpayError("Couldn't reach the payment provider. Try again.") from None
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("description") or resp.text
            except ValueError:
                detail = resp.text
            log.warning("Razorpay %s %s -> %s: %s", method, path, resp.status_code, detail)
            raise RazorpayError(str(detail)[:300], resp.status_code)
        return resp.json()

    async def create_subscription(self, plan_id: str, *, notes: dict) -> dict:
        return await self._call(
            "POST",
            "/subscriptions",
            {"plan_id": plan_id, "total_count": TOTAL_CYCLES, "quantity": 1,
             "customer_notify": 1, "notes": notes},
        )  # fmt: skip

    async def fetch_subscription(self, subscription_id: str) -> dict:
        return await self._call("GET", f"/subscriptions/{subscription_id}")

    async def cancel_subscription(self, subscription_id: str, *, at_cycle_end: bool) -> dict:
        return await self._call(
            "POST",
            f"/subscriptions/{subscription_id}/cancel",
            {"cancel_at_cycle_end": 1 if at_cycle_end else 0},
        )

    async def change_plan(self, subscription_id: str, plan_id: str, *, now: bool) -> dict:
        """Upgrade now (prorated by Razorpay) or downgrade at the end of the paid cycle."""
        return await self._call(
            "PATCH",
            f"/subscriptions/{subscription_id}",
            {"plan_id": plan_id, "schedule_change_at": "now" if now else "cycle_end",
             "customer_notify": 1},
        )  # fmt: skip

    async def fetch_payment(self, payment_id: str) -> dict:
        return await self._call("GET", f"/payments/{payment_id}")

    async def fetch_invoice(self, invoice_id: str) -> dict:
        return await self._call("GET", f"/invoices/{invoice_id}")
