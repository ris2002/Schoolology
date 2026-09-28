"""Test settings. Set before any app module is imported.

Database tests run only against TEST_DATABASE_URL (a separate, empty Supabase
project) and are skipped when it is not set. They never use DATABASE_URL.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

_env_file = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or _env_file.get("TEST_DATABASE_URL") or ""

os.environ.update({
    "DATABASE_URL": TEST_DATABASE_URL,
    "SESSION_SECRET": "test-session-secret",
    "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "ADMIN_PASSWORD": "test-admin-password",
    "DEMO_MODE": "true",
    "HF_TOKEN": "hf_app_test_token",
    "ANTHROPIC_API_KEY": "sk-ant-app-test-key",
    "PUBLIC_URL": "http://testserver",
    "SMTP_HOST": "",
    "SCHOOLOGY_CONSUMER_KEY": "test-consumer-key",
    "SCHOOLOGY_CONSUMER_SECRET": "test-consumer-secret",
    "SCHOOLOGY_ALLOWED_DOMAINS": "*.schoology.com",
    "SCHOOLOGY_PERSONAL_KEY_SIGN_IN": "false",
})
