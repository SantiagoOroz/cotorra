"""Configuration: environment variables + the event manifest (``cotorra.yaml``).

Every knob has a working default. The only thing you *have* to provide to run
with real speech recognition is ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import AliasChoices, AliasGenerator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import SessionSpec

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Every setting can be written two ways: ``PORT`` or ``COTORRA_PORT``.
    # The bare form reads naturally in a .env file (``GEMINI_API_KEY``); the
    # prefixed form avoids collisions when Cotorra shares an environment with
    # other services, which is exactly what happens inside Kubernetes.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        alias_generator=AliasGenerator(
            validation_alias=lambda name: AliasChoices(name, f"cotorra_{name}")
        ),
    )

    # --- server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    public_url: str = Field(default="", description="External URL, used for QR codes / links")
    web_dir: Path = REPO_ROOT / "web"
    data_dir: Path = REPO_ROOT / "data"
    manifest: Path = REPO_ROOT / "cotorra.yaml"

    # --- auth -------------------------------------------------------------
    # Viewers never need a token. Operators and workers do.
    admin_token: str = Field(default="", description="Required for /api write endpoints when set")
    worker_token: str = Field(default="cotorra-dev", description="Shared secret for worker ingest")

    # --- workers ----------------------------------------------------------
    worker_gateway_url: str = Field(
        default="", description="Where workers phone home. Defaults to ws://127.0.0.1:<port>"
    )
    worker_concurrency: int = Field(default=3, description="Overlapping model calls per session")
    max_local_workers: int = Field(
        default=12, description="Refuse to spawn more subprocesses than this on one box"
    )
    worker_restarts: int = Field(default=5, description="Auto-restarts before giving up")
    heartbeat_timeout_s: float = 12.0
    silence_alarm_s: float = Field(
        default=30.0, description="Seconds of dead air before a session is flagged"
    )

    # --- engine -----------------------------------------------------------
    engine: str = Field(default="mock", description="gemini | local | mock")
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Vertex AI instead of AI Studio. This is where Google Cloud credits apply
    # and where quota is per minute rather than per day. Needs a GCP project
    # with billing and Application Default Credentials.
    gemini_use_vertex: bool = False
    gcp_project: str = ""
    gcp_location: str = "us-central1"
    google_application_credentials: str = Field(
        default="",
        description="Service account JSON. Lets one .env hold everything, including workers.",
    )
    gemini_thinking_budget: int = Field(
        default=0, description="0 disables thinking: lowest latency"
    )
    gemini_max_retries: int = 3
    gemini_timeout_s: float = Field(
        default=8.0, description="Per-attempt timeout. Live captions die of old age, not errors."
    )
    gemini_deadline_s: float = Field(
        default=10.0, description="Total budget per utterance across retries"
    )
    gemini_max_retry_wait_s: float = Field(
        default=2.0,
        description="Longest 429 backoff worth taking; beyond it the utterance is dropped",
    )

    local_whisper_model: str = "small"
    local_whisper_device: str = "auto"
    local_whisper_compute_type: str = "int8"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"

    # --- audio segmentation ----------------------------------------------
    sample_rate: int = 16000
    min_segment_ms: int = 900
    max_segment_ms: int = 7000
    silence_ms: int = 420
    preroll_ms: int = 300
    partials: bool = Field(default=True, description="Emit interim captions mid-utterance")
    partial_interval_ms: int = 1800

    # --- fan-out ----------------------------------------------------------
    redis_url: str = Field(default="", description="Set to scale the gateway beyond one replica")
    backlog_size: int = Field(default=40, description="Captions replayed to a joining viewer")
    subscriber_queue: int = Field(default=200, description="Per-viewer buffer before dropping")

    # --- cost model (USD per 1M tokens, see docs/costs.md) ----------------
    price_audio_in: float = 1.00
    price_text_in: float = 0.30
    price_text_out: float = 2.50

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def glossaries_dir(self) -> Path:
        return self.data_dir / "glossaries"

    def apply_google_credentials(self) -> str:
        """Export the service account path so Google's auth library can see it.

        google-auth only reads GOOGLE_APPLICATION_CREDENTIALS from the real
        environment, but pydantic-settings loads .env into Settings and never
        into os.environ. Without this bridge a worker subprocess inherits no
        credentials and every Vertex call 403s, with a .env that looks correct.
        """
        path = self.google_application_credentials or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", ""
        )
        if self.google_application_credentials:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS", self.google_application_credentials
            )
        return path

    def has_google_credentials(self) -> bool:
        """True when something can authenticate to Google Cloud."""
        if self.apply_google_credentials():
            return True
        # gcloud writes ADC to different places on Windows and POSIX.
        adc = "application_default_credentials.json"
        bases = [Path.home() / ".config"]
        appdata = os.environ.get("APPDATA")
        if appdata:
            bases.append(Path(appdata))
        return any((base / "gcloud" / adc).exists() for base in bases)

    def ensure_dirs(self) -> None:
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self.glossaries_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_manifest(path: Path | None = None) -> list[SessionSpec]:
    """Read ``cotorra.yaml`` -> the list of sessions known at boot.

    Missing file is not an error: an event can be driven entirely through the
    REST API instead.
    """
    settings = get_settings()
    path = path or settings.manifest
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults: dict = raw.get("defaults", {}) or {}
    specs: list[SessionSpec] = []
    for entry in raw.get("sessions", []) or []:
        merged = {k: _expand(v) for k, v in {**defaults, **entry}.items()}
        # A session that does not name an engine follows COTORRA_ENGINE.
        # Without this, setting COTORRA_ENGINE in .env would silently not
        # reach the manifest: pydantic-settings reads .env into Settings, not
        # into os.environ, so ${COTORRA_ENGINE} inside the YAML sees nothing.
        if not merged.get("engine"):
            merged["engine"] = settings.engine
        specs.append(SessionSpec(**merged))
    return specs


_VAR_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand(value):
    """Allow ``${VAR}``, ``${VAR:-default}`` and repo-relative paths in the manifest.

    ``os.path.expandvars`` does not understand the ``:-`` default form and
    behaves differently on Windows, so we do it ourselves and get the same
    manifest semantics on every platform.
    """
    if not isinstance(value, str):
        return value
    value = _VAR_RE.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value)
    if value.startswith(("./", "../")):
        return str((REPO_ROOT / value).resolve())
    return value
