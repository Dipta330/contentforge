#!/usr/bin/env python3
"""
Run this ONCE to set up Appwrite collections.
Usage: python setup_appwrite.py
"""

import os
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.enums import IndexType

load_dotenv()

client = Client()
client.set_endpoint(os.environ["APPWRITE_ENDPOINT"])
client.set_project(os.environ["APPWRITE_PROJECT_ID"])
client.set_key(os.environ["APPWRITE_API_KEY"])

db = Databases(client)
DB_ID = os.environ["APPWRITE_DB_ID"]

print("Setting up Appwrite collections...")

# ── users collection ─────────────────────────────────────────────────────────
try:
    db.create_collection(DB_ID, "users", "Users")
    print("✓ Created 'users' collection")
except Exception as e:
    print(f"  'users' already exists or error: {e}")

attrs_users = [
    ("subscription_status", "string", 20, False, "inactive"),
    ("stripe_subscription_id", "string", 100, False, ""),
    ("credits_remaining", "integer", None, False, 0),
]
for name, kind, size, required, default in attrs_users:
    try:
        if kind == "string":
            db.create_string_attribute(DB_ID, "users", name, size, required, default=default)
        elif kind == "integer":
            db.create_integer_attribute(DB_ID, "users", name, required, default=default)
        print(f"  ✓ users.{name}")
    except Exception as e:
        print(f"  users.{name}: {e}")

# ── articles collection ───────────────────────────────────────────────────────
try:
    db.create_collection(DB_ID, "articles", "Articles")
    print("✓ Created 'articles' collection")
except Exception as e:
    print(f"  'articles' already exists or error: {e}")

attrs_articles = [
    ("user_id",    "string",  36,   True,  None),
    ("topic",      "string",  500,  True,  None),
    ("keywords",   "string",  1000, False, "[]"),
    ("title",      "string",  500,  True,  None),
    ("content",    "string",  65535, True, None),
    ("word_count", "integer", None, False, 0),
    ("tone",       "string",  50,   False, "professional"),
]
for name, kind, size, required, default in attrs_articles:
    try:
        if kind == "string":
            kw = {"default": default} if default is not None else {}
            db.create_string_attribute(DB_ID, "articles", name, size, required, **kw)
        elif kind == "integer":
            db.create_integer_attribute(DB_ID, "articles", name, required, default=default)
        print(f"  ✓ articles.{name}")
    except Exception as e:
        print(f"  articles.{name}: {e}")

print("\n✅ Appwrite setup complete!")
print("\nNext steps:")
print("  1. Go to Appwrite console → your DB → articles → Indexes")
print("     Add index: user_id (key) for fast per-user queries")
print("  2. Set collection permissions:")
print("     users:    role:any can read/write their own doc (use document-level security)")
print("     articles: role:any can read/write their own docs")
