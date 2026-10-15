"""Settings loaded from environment variables (.env in the project root)."""

import os
import secrets

from dotenv import load_dotenv

load_dotenv()


class Settings:
    github_client_id: str = os.getenv("GITHUB_CLIENT_ID", "")
    github_client_secret: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    github_redirect_uri: str = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/auth/callback")
    # OAuth scopes. "repo" lets SecureFix read private repos and open fix pull requests.
    # Use "read:user public_repo" if you only work with public repos.
    github_scopes: str = os.getenv("GITHUB_SCOPES", "read:user repo security_events")
    session_secret: str = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
    database_path: str = os.getenv("DATABASE_PATH", "securefix.db")
    # LLM provider: OpenAI (Chat Completions API).
    llm_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    # Smaller, faster model for chat replies (falls back to the main model when unset).
    llm_fast_model: str = os.getenv("LLM_FAST_MODEL", "")
    # Reasoning effort for the code review (models that support it, e.g. gpt-oss on Groq): low | medium | high.
    llm_review_effort: str = os.getenv("LLM_REVIEW_EFFORT", "high")
    demo_mode: bool = os.getenv("DEMO_MODE", "1") == "1"

    # Engine
    engine_mode: str = os.getenv("ENGINE_MODE", "auto")  # auto | replay | live
    runs_dir: str = os.getenv("RUNS_DIR", "runs")
    scan_step_delay: float = float(os.getenv("SCAN_STEP_DELAY", "1.2"))  # seconds between replayed steps
    engine_source_dir: str = os.getenv("ENGINE_SOURCE_DIR", "")  # local checkout scanned in live mode

    # Organization dashboard: how often to look for new GitHub pushes (0 turns the watcher off), and the
    # secret for the GitHub webhook at POST /api/github/webhook (empty turns the webhook off).
    # Security repair agent and its model. LLM_TPM caps tokens per minute (0 = learn it from the provider's
    # rate-limit errors); LLM_MAX_OUTPUT_TOKENS caps each answer; prompts are kept under LLM_MAX_PROMPT_CHARS.
    llm_tpm: int = int(os.getenv("LLM_TPM", "0"))
    llm_fallback_models: str = os.getenv("LLM_FALLBACK_MODELS", "")  # comma separated, tried when busy
    llm_fallback_models: str = os.getenv("LLM_FALLBACK_MODELS", "")  # comma separated, tried when the model is busy
    llm_rpm: int = int(os.getenv("LLM_RPM", "0"))  # requests per minute (Gemini's free tier allows about 10)
    llm_max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2600"))
    llm_max_prompt_chars: int = int(os.getenv("LLM_MAX_PROMPT_CHARS", "13000"))
    fleet_watch_seconds: int = int(os.getenv("FLEET_WATCH_SECONDS", "60"))
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")


settings = Settings()
