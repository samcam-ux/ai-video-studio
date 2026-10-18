"""Stripe checkout for credit packs."""
import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# $9 one-time for 20 video credits
PRICE_ID = os.environ.get("STRIPE_PRICE_CREDITS", "")

def create_checkout(email: str, app_url: str) -> str:
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=email,
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        success_url=f"{app_url}/?paid=1",
        cancel_url=f"{app_url}/?cancelled=1",
        metadata={"email": email, "credits": "20"},
    )
    return session.url

def handle_webhook(payload: bytes, sig: str) -> dict | None:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    event = stripe.Webhook.construct_event(payload, sig, secret)
    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]
        return {
            "email": s["metadata"].get("email"),
            "credits": int(s["metadata"].get("credits", 0)),
        }
    return None


# --- Monthly subscription: $19/mo for 60 video credits ---
# Set STRIPE_PRICE_SUBSCRIPTION to a recurring Price ID in the Stripe dashboard.
SUBSCRIPTION_PRICE_ID = os.environ.get("STRIPE_PRICE_SUBSCRIPTION", "")
SUBSCRIPTION_CREDITS = int(os.environ.get("SUBSCRIPTION_CREDITS", "60"))

def create_subscription_checkout(email: str, app_url: str) -> str:
    """Create a Stripe Checkout session for the monthly subscription."""
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        line_items=[{"price": SUBSCRIPTION_PRICE_ID, "quantity": 1}],
        success_url=f"{app_url}/?subscribed=1",
        cancel_url=f"{app_url}/?cancelled=1",
        metadata={"email": email, "type": "subscription"},
    )
    return session.url


def handle_subscription_webhook(event: dict) -> dict | None:
    """Handle subscription lifecycle events. Returns action dict or None."""
    etype = event["type"]
    obj = event["data"]["object"]
    if etype == "checkout.session.completed":
        s = obj
        if s.get("metadata", {}).get("type") == "subscription":
            return {
                "action": "activate",
                "email": s["metadata"].get("email"),
                "customer_id": s.get("customer"),
            }
    elif etype == "invoice.payment_succeeded":
        inv = obj
        # Monthly renewal: grant credits (skip the first invoice, handled at checkout)
        if not inv.get("billing_reason") == "subscription_create":
            email = (inv.get("customer_email") or "")
            return {"action": "renew", "email": email, "customer_id": inv.get("customer")}
    elif etype == "customer.subscription.deleted":
        sub = obj
        return {"action": "cancel", "customer_id": sub.get("customer")}
    return None
