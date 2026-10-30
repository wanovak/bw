"""Central configuration. Everything is read from environment variables (see .env.example)."""
import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

CENTER_NAME = "Little Acorns Learning Center"
CENTER_PHONE = "(614) 555-0138"
DIRECTOR_NAME = "Maria Delgado"
CATEGORIES = [
    "Hours",
    "Tuition & Fees",
    "Health & Illness",
    "Meals",
    "Tours & Enrollment",
    "Policies",
    "Other",
]

PLACEHOLDER_SECRETS = {"", "change-me", "changeme"}


class Settings:
    def __init__(self) -> None:
        e = os.environ.get
        self.llm_mode = e("LLM_MODE", "gemini").strip().lower()  # gemini | mock
        self.gemini_api_key = e("GEMINI_API_KEY", "").strip()
        self.gemini_model = e("GEMINI_MODEL", "gemini-3.5-flash").strip()
        self.llm_timeout_seconds = float(e("LLM_TIMEOUT_SECONDS", "20"))
        self.jwt_secret = e("JWT_SECRET", "").strip()
        self.parent_id_hmac_secret = e("PARENT_ID_HMAC_SECRET", "").strip()
        self.pii_encryption_key = e("PII_ENCRYPTION_KEY", "").strip()
        self.default_operator_email = e("DEFAULT_OPERATOR_EMAIL", "admin@littleacorns.test").strip().lower()
        self.default_operator_password = e("DEFAULT_OPERATOR_PASSWORD", "").strip()
        self.seed_demo_data = e("SEED_DEMO_DATA", "true").strip().lower() in ("1", "true", "yes")
        self.demo_parent_email = "demo.parent@example.com"
        self.demo_parent_password = e("DEMO_PARENT_PASSWORD", "DemoParent123!")
        self.confidence_threshold = float(e("CONFIDENCE_THRESHOLD", "0.90"))
        self.rate_limit_per_hour = int(e("RATE_LIMIT_PER_HOUR", "20"))
        self.max_question_chars = int(e("MAX_QUESTION_CHARS", "500"))
        self.session_hours = int(e("SESSION_HOURS", "8"))
        self.cookie_secure = e("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
        self.context_char_budget = int(e("CONTEXT_CHAR_BUDGET", "24000"))
        self.data_dir = Path(e("DATA_DIR", "/data"))
        self.seed_dir = Path(e("SEED_DIR", str(Path(__file__).resolve().parent.parent / "seed")))

    @property
    def db_path(self) -> str:
        return str(self.data_dir / "app.db")

    @property
    def fernet_key(self) -> bytes:
        return base64.urlsafe_b64encode(hashlib.sha256(self.pii_encryption_key.encode()).digest())

    def validate(self) -> None:
        problems = []
        for name, value in (
            ("JWT_SECRET", self.jwt_secret),
            ("PARENT_ID_HMAC_SECRET", self.parent_id_hmac_secret),
            ("PII_ENCRYPTION_KEY", self.pii_encryption_key),
        ):
            if value in PLACEHOLDER_SECRETS:
                problems.append(f"{name} is not set (see .env.example)")
        if self.llm_mode not in ("gemini", "mock"):
            problems.append("LLM_MODE must be 'gemini' or 'mock'")
        if self.llm_mode == "gemini" and self.gemini_api_key in PLACEHOLDER_SECRETS:
            problems.append(
                "GEMINI_API_KEY is not set. Create a free key at https://aistudio.google.com/apikey "
                "and put it in .env (or set LLM_MODE=mock to run without Gemini)."
            )
        if problems:
            raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
