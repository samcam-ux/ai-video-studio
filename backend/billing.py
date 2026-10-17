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
