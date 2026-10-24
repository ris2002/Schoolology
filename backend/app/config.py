"""Settings, read once from the environment (or a .env file in the repo root)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# Web app
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
DEMO_MODE = _bool("DEMO_MODE", True)
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")  # lets a scheduler call /cron/<job>; unset turns those routes off

# Supabase Postgres
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Schoology (three-legged OAuth 1.0a)
SCHOOLOGY_CONSUMER_KEY = os.getenv("SCHOOLOGY_CONSUMER_KEY", "")
SCHOOLOGY_CONSUMER_SECRET = os.getenv("SCHOOLOGY_CONSUMER_SECRET", "")
SCHOOLOGY_API_BASE = os.getenv("SCHOOLOGY_API_BASE", "https://api.schoology.com/v1")
SCHOOLOGY_ALLOWED_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("SCHOOLOGY_ALLOWED_DOMAINS", "*.schoology.com").split(",")
    if d.strip()
]
# Districts offered in the sign-in page's dropdown: "Name=domain,Name=domain".
# Their domains are always allowed, even outside SCHOOLOGY_ALLOWED_DOMAINS.
# Defaults are addresses whose login page names the district (checked Sept 2026).
SCHOOLOGY_DISTRICTS = [
    {"name": name.strip(), "domain": domain.strip().lower()}
    for name, _, domain in (
        entry.partition("=")
        for entry in os.getenv(
            "SCHOOLOGY_DISTRICTS",
            "Coppell ISD=schoology.coppellisd.com,Irving ISD=irvingisd.schoology.com,Grand Prairie ISD=gpisd.schoology.com",
        ).split(",")
    )
    if name.strip() and domain.strip()
]
# Developer only: sign in directly as the owner of SCHOOLOGY_CONSUMER_KEY when it is a
# personal key from <district>/api (two-legged OAuth, no approval page). Works only for
# requests made on this computer, or after signing in at /admin. On a public server the
# key owner's data is then protected by ADMIN_PASSWORD alone, so make it long and unique.
SCHOOLOGY_PERSONAL_KEY_SIGN_IN = _bool("SCHOOLOGY_PERSONAL_KEY_SIGN_IN", False)
# Paper uploads a parent may make per day. Reading a paper is the most expensive AI call.
PAPER_UPLOADS_PER_DAY = int(os.getenv("PAPER_UPLOADS_PER_DAY", "10"))
# Schoology due dates have no timezone; they are read in the school's local time.
SCHOOL_TIMEZONE = os.getenv("SCHOOL_TIMEZONE", "America/Chicago")
# Schoology access tokens expire 90 days after they are issued.
SCHOOLOGY_TOKEN_DAYS = 90
# Ask the parent to reconnect this many days before the token expires.
SCHOOLOGY_RECONNECT_WARNING_DAYS = 7

# AI providers: the app's own keys. Provider and model are admin settings in the database.
HF_TOKEN = os.getenv("HF_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_PROVIDER = "huggingface"
DEFAULT_MODELS = {
    "huggingface": "Qwen/Qwen2.5-72B-Instruct",
    "anthropic": "claude-opus-5",
}

# WhatsApp (Meta WhatsApp Business Platform, Cloud API). Until these are set, outgoing messages
# are written to the log instead of sent, so everything else can be built and tested.
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")                  # system user access token
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")  # the sending number's id (not the number)
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")    # any secret you choose; entered in Meta's webhook setup
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")        # the Meta app secret, to check webhook signatures
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "")                # the bot's number in international form, e.g. 15551234567
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")  # Graph API version; update when Meta retires it

# Stripe: verifies the payment confirmations Stripe sends to /stripe/webhook
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Optional email delivery for weekly summaries and alerts
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

# Rule thresholds. Starting examples from the plan, to be tuned in the pilot.
THRESHOLDS = {
    "weak_topic_min_assignments": 3,  # topic missed in 3+ assignments is flagged
    "gap_points": 10,  # homework avg minus test avg
    "alert_average_drop": 10,  # course average down 10+ points in a week
    "alert_low_test_score": 60,  # a test or quiz graded below this percent
    "weekly_grade_move": 3,  # grade move worth mentioning in the weekly summary
    "effort_missing_rate": 0.20,  # share of due work missing or late
    "effort_completed_avg": 75,  # scores are fine when work is done
    "on_time_rate": 0.90,  # "work on time"
    "low_score": 70,  # "low scores"
    "sudden_drop_points": 10,  # drop across all subjects over two weeks
}


def check_required() -> list[str]:
    """Settings the server cannot run safely without."""
    missing = [
        name
        for name, value in [
            ("SESSION_SECRET", SESSION_SECRET),
            ("TOKEN_ENCRYPTION_KEY", TOKEN_ENCRYPTION_KEY),
            ("DATABASE_URL", DATABASE_URL),
        ]
        if not value
    ]
    return missing
