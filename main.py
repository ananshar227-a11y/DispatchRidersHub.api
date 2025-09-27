
# main.py
import os, hmac, hashlib, json
from typing import Any, Dict, Optional, Literal

import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ───────────────────────── App & Settings ─────────────────────────

app = FastAPI(title="Dispatch Hub API", version="1.0.0")

# CORS (you can restrict origins later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config file (optional — safe if missing)
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG: Dict[str, Any] = json.load(f)
else:
    CONFIG = {}

# Env vars from Render Settings → Environment
PAYSTACK_SECRET = os.getenv("PAYSTACK_SECRET", "sk_test_demo")
WEBHOOK_VERIFY = os.getenv("WEBHOOK_VERIFY", "disable")  # "disable" | "paystack"
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "").strip()  # your Make.com hook

# Very small in-memory stores (for demo)
DB_TRANSACTIONS: Dict[str, Dict[str, Any]] = {}
DB_POSTS: Dict[str, Dict[str, Any]] = {}

# ───────────────────────── Models ─────────────────────────

class CreatePaymentBody(BaseModel):
    user_id: str
    amount: int
    currency: Literal["NGN"] = "NGN"
    email: str
    reference: Optional[str] = None
    callback_url: Optional[str] = None

class CreatePostBody(BaseModel):
    intent: Literal["hire", "update", "promo"] = "hire"
    post_copy: str
    visibility_plan: Literal["standard", "boost_24h", "featured_48h", "pinned_48h", "sponsored", "deals", "subscription"] = "standard"
    user_id: str
    use_disclaimer: bool = True
    media_url: Optional[str] = None

class LogCRMBody(BaseModel):
    table: Literal["Users", "Posts", "Transactions", "Alerts", "Moderation"] = "Posts"
    record: Dict[str, Any]

class SendAlertBody(BaseModel):
    user_id: str
    alert_type: Literal["jobs_near_me", "verify", "report_abuse", "other"] = "jobs_near_me"
    payload: Dict[str, Any] = {}

# ───────────────────────── Helpers ─────────────────────────

def _verify_paystack_signature(signature: str, raw_body: bytes) -> bool:
    """
    Paystack signs webhooks with HMAC SHA512 using your secret key.
    """
    if not signature:
        return False
    mac = hmac.new(PAYSTACK_SECRET.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha512)
    return hmac.compare_digest(mac.hexdigest(), signature)

async def _forward_to_make(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send any CRM/alert payload to your Make.com webhook (if configured).
    """
    if not MAKE_WEBHOOK_URL:
        # Not configured; treat as no-op
        return {"forwarded": False, "reason": "MAKE_WEBHOOK_URL not set"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(MAKE_WEBHOOK_URL, json=payload)
        return {"forwarded": True, "status_code": r.status_code}
    except Exception as e:
        return {"forwarded": False, "error": str(e)}

# ───────────────────────── Endpoints ─────────────────────────

@app.post("/get-config", summary="Get Config")
async def get_config():
    """
    Returns server/runtime config for the client (pricing, disclaimers, timezone, etc.)
    """
    return {
        **CONFIG,
        "PRICES": {
            "STANDARD_JOB": 2000,
            "FEATURED_48H": 5000,
            "BOOST_24H": 500,
            "VERIFIED_BADGE": 1500,
            "SPONSORED": 3000,
            "PINNED_48H": 5000,
            "DEALS": 2000,
            "SUBSCRIPTION": 3000,
        },
        "DISCLAIMER": (
            "Disclaimer: All business is at your own risk. "
            "DISPATCH RIDERS HUB NIGERIA is a listing platform only and is not liable for disputes."
        ),
        "TZ": "Africa/Lagos",
    }

@app.post("/create-payment", summary="Create Payment")
async def create_payment(body: CreatePaymentBody):
    """
    Creates a payment intent (demo placeholder) and stores a transaction.
    """
    ref = body.reference or f"REF-{body.user_id}-{body.amount}"
    DB_TRANSACTIONS[ref] = {"reference": ref, "amount": body.amount, "status": "pending", "user_id": body.user_id}
    # Paystack example checkout URL placeholder; your frontend will actually initialize with Paystack JS
    payment_url = f"https://paystack.com/pay/{ref}"
    return {"payment_url": payment_url, "reference": ref}

@app.post("/payment-webhook", summary="Payment Webhook (Paystack)")
async def payment_webhook(
    request: Request,
    http_x_paystack_signature: Optional[str] = Header(default=None, alias="x-paystack-signature"),
):
    """
    Receives Paystack webhooks. If WEBHOOK_VERIFY=paystack, verifies signature.
    """
    raw = await request.body()
    event = await request.json()

    if WEBHOOK_VERIFY == "paystack":
        if not http_x_paystack_signature or not _verify_paystack_signature(http_x_paystack_signature, raw):
            raise HTTPException(status_code=401, detail="Invalid signature")

    ref = event.get("data", {}).get("reference") or event.get("reference")
    status = (event.get("data", {}).get("status") or event.get("status") or "").lower()

    if ref and ref in DB_TRANSACTIONS:
        if status in {"success", "paid"}:
            DB_TRANSACTIONS[ref]["status"] = "paid"

    # Optionally forward webhook to Make for logging
    await _forward_to_make({"type": "payment_webhook", "payload": event})

    return {"ok": True, "reference": ref}

@app.post("/create-post", summary="Create Post")
async def create_post(body: CreatePostBody):
    """
    Saves a post request and returns a queued status + (placeholder) group link.
    """
    post_id = f"post_{len(DB_POSTS) + 1}"
    copy = body.post_copy
    if body.use_disclaimer:
        footer = CONFIG.get("DISCLAIMER", "")
        if footer and footer not in copy:
            copy = f"{copy}\n{footer}"

    DB_POSTS[post_id] = {
        "intent": body.intent,
        "post_copy": copy,
        "media_url": body.media_url,
        "visibility_plan": body.visibility_plan,
        "user_id": body.user_id,
        "status": "queued",
    }

    # Forward to Make so you can send to Sheets/CRM/FB via your scenario
    await _forward_to_make({"type": "create_post", "post_id": post_id, "record": DB_POSTS[post_id]})

    group_link = "https://facebook.com/groups/YOUR_GROUP_ID/posts/post_1"  # placeholder
    return {"status": "queued", "group_link": group_link, "id": post_id}

@app.post("/log-crm", summary="Log Crm")
async def log_crm(body: LogCRMBody):
    """
    Generic CRM logger → forwards to Make scenario.
    """
    payload = {"type": "crm_log", "table": body.table, "record": body.record}
    fwd = await _forward_to_make(payload)
    return {"ok": True, "id": body.record.get("id", "post_1"), "forward": fwd}

@app.post("/send-alert", summary="Send Alert")
async def send_alert(body: SendAlertBody):
    """
    Queues an alert and forwards to Make for delivery (SMS/WhatsApp/Email/push).
    """
    payload = {"type": "alert", "user_id": body.user_id, "alert_type": body.alert_type, "payload": body.payload}
    fwd = await _forward_to_make(payload)
    return {"status": "queued", "forward": fwd}

@app.get("/health", summary="Health")
async def health():
    return {"ok": True}
