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


# --- B2B monthly subscriptions ---
# Set these to recurring Price IDs in the Stripe dashboard.
TIERS = {
    "starter": {
        "price_id": os.environ.get("STRIPE_PRICE_STARTER", ""),
        "credits": int(os.environ.get("STARTER_CREDITS", "150")),
        "label": "Starter ($49/mo)",
    },
    "professional": {
        "price_id": os.environ.get("STRIPE_PRICE_PROFESSIONAL", ""),
        "credits": int(os.environ.get("PROFESSIONAL_CREDITS", "500")),
        "label": "Professional ($149/mo)",
    },
}
# Backwards-compat single values (unused by new flow)
SUBSCRIPTION_PRICE_ID = TIERS["starter"]["price_id"]
SUBSCRIPTION_CREDITS = TIERS["starter"]["credits"]

def create_subscription_checkout(email: str, app_url: str, tier: str = "starter") -> str:
    """Create a Stripe Checkout session for a monthly subscription tier."""
    t = TIERS.get(tier, TIERS["starter"])
    if not t["price_id"]:
        raise RuntimeError(f"Stripe price not configured for tier '{tier}'")
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        line_items=[{"price": t["price_id"], "quantity": 1}],
        success_url=f"{app_url}/?subscribed=1",
        cancel_url=f"{app_url}/?cancelled=1",
        metadata={"email": email, "type": "subscription", "tier": tier},
    )
    return session.url


def handle_subscription_webhook(event: dict) -> dict | None:
    """Handle subscription lifecycle events. Returns action dict or None."""
    etype = event["type"]
    obj = event["data"]["object"]
    if etype == "checkout.session.completed":
        s = obj
        if s.get("metadata", {}).get("type") == "subscription":
            tier = s["metadata"].get("tier", "starter")
            return {
                "action": "activate",
                "email": s["metadata"].get("email"),
                "customer_id": s.get("customer"),
                "tier": tier,
                "credits": TIERS.get(tier, TIERS["starter"])["credits"],
            }
    elif etype == "invoice.payment_succeeded":
        inv = obj
        # Monthly renewal: grant credits (skip the first invoice, handled at checkout)
        if not inv.get("billing_reason") == "subscription_create":
            email = (inv.get("customer_email") or "")
            # Look up tier from the subscription if possible; default to starter
            return {"action": "renew", "email": email, "customer_id": inv.get("customer")}
    elif etype == "customer.subscription.deleted":
        sub = obj
        return {"action": "cancel", "customer_id": sub.get("customer")}
    return None
