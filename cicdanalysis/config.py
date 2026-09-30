from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _first(*names: str, default: str = "") -> str:
    """First non-empty variable; LLM_* settings also accept the AI_* names used in existing .env files."""
    return next((os.getenv(n) for n in names if os.getenv(n)), default)


def _first_int(*names: str, default: int) -> int:
    try:
        return int(_first(*names, default=str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = _int("APP_PORT", 8080)
    timezone: str = os.getenv("APP_TIMEZONE", "Asia/Kuala_Lumpur")
    database_path: str = os.getenv("DATABASE_PATH", "./data/cicdanalysis.db")
    database_url: str = os.getenv("DATABASE_URL", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    jenkins_url: str = os.getenv("JENKINS_URL", "").rstrip("/")
    jenkins_user: str = os.getenv("JENKINS_USER", "")
    jenkins_token: str = os.getenv("JENKINS_API_TOKEN", "")
    jenkins_verify_tls: bool = _bool("JENKINS_VERIFY_TLS", True)
    poll_seconds: int = _int("JENKINS_POLL_SECONDS", 60)
    initial_build_limit: int = _int("JENKINS_INITIAL_BUILD_LIMIT", 20)
    console_max_bytes: int = _int("JENKINS_CONSOLE_MAX_BYTES", 524288)
    console_head_bytes: int = _int("JENKINS_CONSOLE_HEAD_BYTES", 65536)
    jenkins_service_users: str = os.getenv("JENKINS_SERVICE_USERS", "ugadmin,cicd-analysis,anonymous")
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_listen_group_messages: bool = _bool("TELEGRAM_LISTEN_GROUP_MESSAGES", True)
    telegram_poll_timeout: int = _int("TELEGRAM_POLL_TIMEOUT", 30)
    # Optional user-account (MTProto) reader for the release groups; see userbot.py.
    telegram_user_api_id: str = os.getenv("TELEGRAM_USER_API_ID", "")
    telegram_user_api_hash: str = os.getenv("TELEGRAM_USER_API_HASH", "")
    telegram_user_session: str = os.getenv("TELEGRAM_USER_SESSION", "/data/telegram-user")
    telegram_user_trusted_senders: str = os.getenv("TELEGRAM_USER_TRUSTED_SENDERS", "")
    telegram_user_backfill_days: int = _int("TELEGRAM_USER_BACKFILL_DAYS", 2)
    # LLM root-cause analysis (OpenAI-compatible Chat Completions; DeepSeek by default).
    llm_base_url: str = _first("LLM_BASE_URL", "AI_BASE_URL", default="https://api.deepseek.com")
    llm_api_key: str = _first("LLM_API_KEY", "AI_API_KEY")
    llm_model: str = _first("LLM_MODEL", "AI_MODEL", default="deepseek-chat")
    llm_timeout: int = _first_int("LLM_TIMEOUT", "AI_TIMEOUT", default=60)
    llm_max_tokens: int = _first_int("LLM_MAX_TOKENS", "AI_MAX_TOKENS", default=1200)
    llm_max_input_chars: int = _int("LLM_MAX_INPUT_CHARS", 16000)
    llm_reuse_hours: int = _int("LLM_REUSE_HOURS", 24)
    testa_chat_id: str = os.getenv("TELEGRAM_TESTA_CHAT_ID", "-1003919548725")
    uat_chat_id: str = os.getenv("TELEGRAM_UAT_CHAT_ID", "-5592801576")
    prod_chat_id: str = os.getenv("TELEGRAM_PROD_CHAT_ID", "-1003412281586")
    management_chat_id: str = os.getenv("TELEGRAM_MANAGEMENT_CHAT_ID", "")
    notify_failures: bool = _bool("TELEGRAM_NOTIFY_FAILURES", True)
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    trigger_webhook_secret: str = os.getenv("TRIGGER_WEBHOOK_SECRET", os.getenv("WEBHOOK_SECRET", ""))
    # Read (GET) endpoints: dashboard, /api/v1/*, API docs. Unset means every read request is rejected with 401.
    read_api_token: str = os.getenv("READ_API_TOKEN", "")
    public_health: bool = _bool("PUBLIC_HEALTH", True)    # /healthz, /readyz without a token (probes)
    public_metrics: bool = _bool("PUBLIC_METRICS", True)  # /metrics without a token (Prometheus scrape)
    daily_hour: int = _int("REPORT_DAILY_HOUR", 9)
    daily_minute: int = _int("REPORT_DAILY_MINUTE", 0)
    weekly_weekday: int = _int("REPORT_WEEKLY_WEEKDAY", 0)
    monthly_day: int = _int("REPORT_MONTHLY_DAY", 1)

    def chat_for(self, environment: str) -> str:
        return {"testa": self.testa_chat_id, "test": self.testa_chat_id,
                "uat": self.uat_chat_id, "prod": self.prod_chat_id}.get(environment.lower(), "")

    def chat_environments(self) -> dict[str, str]:
        """Release-group chat id -> environment; the group a message came from decides its environment."""
        pairs = ((self.testa_chat_id, "testa"), (self.uat_chat_id, "uat"), (self.prod_chat_id, "prod"))
        return {str(chat): env for chat, env in pairs if chat}

    def telegram_allowed_chats(self) -> set[str]:
        return {str(x) for x in (self.testa_chat_id,self.uat_chat_id,self.prod_chat_id,self.management_chat_id) if x}
