main.py
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
import os, hmac, hashlib, json

app = FastAPI(title="Dispatch Hub API", version="1.0.0")

# Allow all origins (can restrict later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Load CONFIG from config.json ----
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
else:
    CONFIG = {}

PAYSTACK_SECRET = os.getenv("PAYSTACK_SECRET", "sk_test_demo")
WEBHOOK_VERIFY = os.getenv("WEBHOOK_VERIFY", "disable")

# In-memory databases (replace later with Airtable/Sheets/DB)
DB_TRANSACTIONS: Dict[str, Dict[str, Any]] = {}
DB_POSTS: Dict[str, Dict[str, Any]] = {}

# ----------------- MODELS -----------------
class CreatePaymentBody(BaseModel):
    user_id: str
    amount: int
    intent: Optional[str] = None
    package_code: Optional[str] = None

class CreatePostBody(BaseModel):
    intent: str
    post_copy: str
    visibility_plan: Literal["standard","featured"]
    user_id: str
    use_disclaimer: Optional[bool] = True
    media_url: Optional[str] = None

class LogCRMBody(BaseModel):
    table: Literal["Users","Posts","Transactions","Alerts","Moderation"]
    record: Dict[str, Any]

class SendAlertBody(BaseModel):
    user_id: str
    alert_type: Literal["jobs_near_me","verified_riders","gear_deals"]
    payload: Dict[str, Any]

# ----------------- HELPERS -----------------
def _verify_paystack_signature(signature: str, payload: bytes) -> bool:
    mac = hmac.new(PAYSTACK_SECRET.encode("utf-8"), msg=payload, digestmod=hashlib.sha512)
    return hmac.compare_digest(mac.hexdigest(), signature or "")

# ----------------- ENDPOINTS -----------------
@app.post("/get-config")
async def get_config():
    return CONFIG

@app.post("/create-payment")
async def create_payment(body: CreatePaymentBody):
    ref = f"REF-{body.user_id}-{body.amount}"
    DB_TRANSACTIONS[ref] = {"reference": ref, "amount": body.amount, "status": "pending"}
    payment_url = f"https://paystack.com/pay/{ref}"  # Placeholder link
    return {"payment_url": payment_url, "reference": ref}

@app.post("/payment-webhook")
async def payment_webhook(request: Request, http_x_paystack_signature: Optional[str] = Header(None)):
    raw = await request.body()
    event = await request.json()
    if WEBHOOK_VERIFY == "paystack":
        if not http_x_paystack_signature or not _verify_paystack_signature(http_x_paystack_signature, raw):
            raise HTTPException(status_code=401, detail="Invalid signature")
    ref = event.get("data", {}).get("reference") or event.get("reference")
    if ref and ref in DB_TRANSACTIONS:
        DB_TRANSACTIONS[ref]["status"] = "paid"
    return {"ok": True, "reference": ref}

@app.post("/create-post")
async def create_post(body: CreatePostBody):
    post_id = f"post_{len(DB_POSTS)+1}"
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
        "group_link": f"https://facebook.com/groups/YOUR_GROUP_ID/posts/{post_id}"
    }
    return {"status": "queued", "group_link": DB_POSTS[post_id]["group_link"]}

@app.post("/log-crm")
async def log_crm(body: LogCRMBody):
    return {"ok": True, "id": f"{body.table.lower()}_{len(DB_POSTS)+len(DB_TRANSACTIONS)+1}"}

@app.post("/send-alert")
async def send_alert(body: SendAlertBody):
    return {"sent": True}

@app.get("/health")
async def health():
    return {"ok": True}
