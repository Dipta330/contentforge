"""
ContentForge - AI Blog Generator SaaS
Backend: Flask + Appwrite + Lemon Squeezy + HuggingFace
"""

import os
import json
import hmac
import hashlib
import requests
import uuid
import re
from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.account import Account
from appwrite.query import Query

app = Flask(__name__)
CORS(app, origins=[os.environ.get("FRONTEND_URL", "*")])

# ── Lemon Squeezy ─────────────────────────────────────────────────────────────
LS_API_KEY        = os.environ["LS_API_KEY"]
LS_STORE_ID       = os.environ["LS_STORE_ID"]
LS_VARIANT_ID     = os.environ["LS_VARIANT_ID"]
LS_WEBHOOK_SECRET = os.environ["LS_WEBHOOK_SECRET"]
LS_API_BASE       = "https://api.lemonsqueezy.com/v1"

# ── Appwrite ──────────────────────────────────────────────────────────────────
AW_ENDPOINT  = os.environ["APPWRITE_ENDPOINT"]
AW_PROJECT   = os.environ["APPWRITE_PROJECT_ID"]
AW_API_KEY   = os.environ["APPWRITE_API_KEY"]
DB_ID        = os.environ["APPWRITE_DB_ID"]
USERS_COL    = "users"
ARTICLES_COL = "articles"

aw_client = Client()
aw_client.set_endpoint(AW_ENDPOINT).set_project(AW_PROJECT).set_key(AW_API_KEY)
db = Databases(aw_client)

# ── HuggingFace ───────────────────────────────────────────────────────────────
HF_API_KEY = os.environ.get("HF_API_KEY", "")
HF_MODEL   = os.environ.get("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def ls_headers():
    return {
        "Authorization": f"Bearer {LS_API_KEY}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

def get_user_doc(user_id):
    try:
        return db.get_document(DB_ID, USERS_COL, user_id)
    except Exception:
        return None

def is_subscribed(user_id):
    doc = get_user_doc(user_id)
    return bool(doc and doc.get("subscription_status") == "active")

def remaining_credits(user_id):
    doc = get_user_doc(user_id)
    return doc.get("credits_remaining", 0) if doc else 0

def deduct_credit(user_id):
    doc = get_user_doc(user_id)
    credits = doc.get("credits_remaining", 0)
    db.update_document(DB_ID, USERS_COL, user_id, {"credits_remaining": max(0, credits - 1)})

def upsert_user(user_id, payload):
    doc = get_user_doc(user_id)
    if doc:
        db.update_document(DB_ID, USERS_COL, user_id, payload)
    else:
        db.create_document(DB_ID, USERS_COL, user_id, payload)

def get_jwt_user(req):
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, jsonify({"error": "Unauthorized"}), 401
    jwt = auth.split(" ", 1)[1]
    user_client = Client()
    user_client.set_endpoint(AW_ENDPOINT).set_project(AW_PROJECT).set_jwt(jwt)
    try:
        user = Account(user_client).get()
        return user, None, None
    except Exception:
        return None, jsonify({"error": "Invalid token"}), 401

def generate_article(topic, keywords, tone="professional"):
    kw_str = ", ".join(keywords) if keywords else topic
    prompt = f"""[INST] You are an expert SEO content writer. Write a complete, high-quality blog post about: "{topic}"

Keywords to include naturally: {kw_str}
Tone: {tone}

Structure:
1. Attention-grabbing H1 title
2. Compelling introduction (2-3 paragraphs)
3. 4-6 main sections with H2 headings
4. Practical tips in each section
5. Strong conclusion with call-to-action

Write at least 800 words. Output clean HTML with heading tags. [/INST]"""

    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 1500, "temperature": 0.7, "top_p": 0.9, "return_full_text": False},
    }
    resp = requests.post(HF_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    content = data[0].get("generated_text", "") if isinstance(data, list) and data else str(data)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else topic
    return {"title": title, "content": content, "word_count": len(content.split())}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ContentForge API"})


@app.route("/api/checkout", methods=["POST"])
def create_checkout():
    data    = request.get_json()
    user_id = data.get("user_id")
    email   = data.get("email")
    if not user_id or not email:
        return jsonify({"error": "user_id and email required"}), 400

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": email,
                    "custom": {"user_id": user_id},
                },
                "product_options": {
                    "redirect_url": os.environ["FRONTEND_URL"] + "/dashboard.html?subscribed=true",
                },
            },
            "relationships": {
                "store":   {"data": {"type": "stores",   "id": str(LS_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(LS_VARIANT_ID)}},
            },
        }
    }
    resp = requests.post(f"{LS_API_BASE}/checkouts", headers=ls_headers(), json=payload, timeout=30)
    if not resp.ok:
        return jsonify({"error": "Failed to create checkout", "detail": resp.text}), 500
    return jsonify({"checkout_url": resp.json()["data"]["attributes"]["url"]})


@app.route("/api/webhook/lemonsqueezy", methods=["POST"])
def ls_webhook():
    payload   = request.data
    signature = request.headers.get("X-Signature", "")
    secret    = LS_WEBHOOK_SECRET.encode("utf-8")
    digest    = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature):
        abort(400)

    event      = request.get_json()
    event_name = event.get("meta", {}).get("event_name", "")
    attrs      = event.get("data", {}).get("attributes", {})
    custom     = event.get("meta", {}).get("custom_data", {})
    user_id    = custom.get("user_id")
    sub_id     = str(event.get("data", {}).get("id", ""))

    if not user_id:
        return jsonify({"received": True})

    if event_name == "subscription_created":
        upsert_user(user_id, {"subscription_status": "active", "ls_subscription_id": sub_id, "credits_remaining": 30})

    elif event_name == "subscription_updated":
        status = attrs.get("status", "")
        if status == "active":
            upsert_user(user_id, {"subscription_status": "active", "ls_subscription_id": sub_id})
        elif status in ("cancelled", "expired", "paused", "unpaid"):
            upsert_user(user_id, {"subscription_status": "inactive"})

    elif event_name in ("subscription_cancelled", "subscription_expired"):
        upsert_user(user_id, {"subscription_status": "inactive"})

    elif event_name == "subscription_resumed":
        upsert_user(user_id, {"subscription_status": "active"})

    elif event_name == "subscription_payment_success":
        doc = get_user_doc(user_id)
        if doc:
            db.update_document(DB_ID, USERS_COL, user_id, {"credits_remaining": 30, "subscription_status": "active"})

    return jsonify({"received": True})


@app.route("/api/generate", methods=["POST"])
def generate():
    user, err_resp, err_code = get_jwt_user(request)
    if err_resp:
        return err_resp, err_code
    user_id = user["$id"]

    if not is_subscribed(user_id):
        return jsonify({"error": "Active subscription required"}), 403
    if remaining_credits(user_id) <= 0:
        return jsonify({"error": "No credits remaining. Resets next billing cycle."}), 429

    data     = request.get_json()
    topic    = data.get("topic", "").strip()
    keywords = data.get("keywords", [])
    tone     = data.get("tone", "professional")

    if not topic:
        return jsonify({"error": "topic is required"}), 400

    try:
        result = generate_article(topic, keywords, tone)
    except Exception as e:
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500

    article_id = str(uuid.uuid4()).replace("-", "")[:20]
    db.create_document(DB_ID, ARTICLES_COL, article_id, {
        "user_id": user_id, "topic": topic, "keywords": json.dumps(keywords),
        "title": result["title"], "content": result["content"],
        "word_count": result["word_count"], "tone": tone,
    })
    deduct_credit(user_id)

    return jsonify({
        "id": article_id, "title": result["title"], "content": result["content"],
        "word_count": result["word_count"], "credits_remaining": remaining_credits(user_id),
    })


@app.route("/api/articles", methods=["GET"])
def list_articles():
    user, err_resp, err_code = get_jwt_user(request)
    if err_resp:
        return err_resp, err_code
    res = db.list_documents(DB_ID, ARTICLES_COL, [
        Query.equal("user_id", user["$id"]),
        Query.order_desc("$createdAt"),
        Query.limit(50),
    ])
    return jsonify({"articles": [{"id": d["$id"], "title": d["title"], "topic": d["topic"], "word_count": d["word_count"], "created_at": d["$createdAt"]} for d in res.get("documents", [])]})


@app.route("/api/articles/<article_id>", methods=["GET"])
def get_article(article_id):
    user, err_resp, err_code = get_jwt_user(request)
    if err_resp:
        return err_resp, err_code
    doc = db.get_document(DB_ID, ARTICLES_COL, article_id)
    if doc["user_id"] != user["$id"]:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(doc)


@app.route("/api/me", methods=["GET"])
def me():
    user, err_resp, err_code = get_jwt_user(request)
    if err_resp:
        return err_resp, err_code
    doc = get_user_doc(user["$id"])
    return jsonify({
        "user_id": user["$id"], "email": user["email"],
        "subscription_status": doc.get("subscription_status", "inactive") if doc else "inactive",
        "credits_remaining": doc.get("credits_remaining", 0) if doc else 0,
    })


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_ENV") == "development", port=5000)
