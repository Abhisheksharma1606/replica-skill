"""Runtime settings, read once from environment variables.

Every external integration has a DRY_RUN path so the whole pipeline can be
exercised end to end without real keys. DRY_RUN defaults to on: you must opt
in to spending real money or messaging real people.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # Agency identity
    agency_name: str = field(default_factory=lambda: os.environ.get("AGENCY_NAME", "Northstar Ads"))
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"))
    default_currency: str = field(default_factory=lambda: os.environ.get("DEFAULT_CURRENCY", "INR"))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))

    # LLM
    model: str = field(default_factory=lambda: os.environ.get("AGENCY_MODEL", "claude-opus-5-5"))
    brain_effort: str = field(default_factory=lambda: os.environ.get("BRAIN_EFFORT", "medium"))
    specialist_effort: str = field(default_factory=lambda: os.environ.get("SPECIALIST_EFFORT", "high"))
    max_tool_iterations: int = field(default_factory=lambda: int(os.environ.get("MAX_TOOL_ITERATIONS", "12")))

    # State and memory
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", ""))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    voyage_api_key: str = field(default_factory=lambda: os.environ.get("VOYAGE_API_KEY", ""))
    files_dir: str = field(default_factory=lambda: os.environ.get("FILES_DIR", "./generated"))

    # Channels
    whatsapp_token: str = field(default_factory=lambda: os.environ.get("WHATSAPP_TOKEN", ""))
    whatsapp_phone_number_id: str = field(default_factory=lambda: os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""))
    whatsapp_verify_token: str = field(default_factory=lambda: os.environ.get("WHATSAPP_VERIFY_TOKEN", ""))
    whatsapp_app_secret: str = field(default_factory=lambda: os.environ.get("WHATSAPP_APP_SECRET", ""))
    voice_webhook_secret: str = field(default_factory=lambda: os.environ.get("VOICE_WEBHOOK_SECRET", ""))

    # Payments
    stripe_secret_key: str = field(default_factory=lambda: os.environ.get("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(default_factory=lambda: os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
    razorpay_key_id: str = field(default_factory=lambda: os.environ.get("RAZORPAY_KEY_ID", ""))
    razorpay_key_secret: str = field(default_factory=lambda: os.environ.get("RAZORPAY_KEY_SECRET", ""))
    razorpay_webhook_secret: str = field(default_factory=lambda: os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""))

    # Ad account access
    leadsie_connect_url: str = field(default_factory=lambda: os.environ.get("LEADSIE_CONNECT_URL", ""))
    leadsie_webhook_secret: str = field(default_factory=lambda: os.environ.get("LEADSIE_WEBHOOK_SECRET", ""))

    # Ad platforms
    meta_access_token: str = field(default_factory=lambda: os.environ.get("META_ACCESS_TOKEN", ""))
    meta_api_version: str = field(default_factory=lambda: os.environ.get("META_API_VERSION", "v21.0"))
    meta_page_id: str = field(default_factory=lambda: os.environ.get("META_PAGE_ID", ""))
    tiktok_access_token: str = field(default_factory=lambda: os.environ.get("TIKTOK_ACCESS_TOKEN", ""))

    # Human in the loop
    slack_bot_token: str = field(default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", ""))
    slack_signing_secret: str = field(default_factory=lambda: os.environ.get("SLACK_SIGNING_SECRET", ""))
    slack_approval_channel: str = field(default_factory=lambda: os.environ.get("SLACK_APPROVAL_CHANNEL", "#ad-approvals"))
    # Slack user ids allowed to approve launches (comma separated). Empty = anyone in the channel.
    slack_approver_ids: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip() for x in os.environ.get("SLACK_APPROVER_IDS", "").split(",") if x.strip()))
    approval_ttl_hours: float = field(default_factory=lambda: _float("APPROVAL_TTL_HOURS", 48.0))

    # Web control room (/admin). Required unless DRY_RUN is on.
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))

    # Background jobs (n8n / Temporal / cron call these endpoints)
    jobs_token: str = field(default_factory=lambda: os.environ.get("JOBS_TOKEN", ""))


def get_settings() -> Settings:
    return Settings()
