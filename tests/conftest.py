"""Keep the suite hermetic.

Settings reads a .env file and the process environment. A developer with a
configured .env — Vertex on, a project set, a different model — would then get
different test results from CI, which is exactly the kind of "works on my
machine" that wastes an afternoon. Here we cut both off so every test sees the
declared defaults and nothing else.
"""

from __future__ import annotations

import pytest

from cotorra.config import Settings, get_settings

# Anything Settings would pick up from the ambient environment.
LEAKY = [
    "COTORRA_ENGINE", "ENGINE",
    "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_USE_VERTEX",
    "GEMINI_THINKING_BUDGET", "GEMINI_TIMEOUT_S", "GEMINI_DEADLINE_S",
    "GCP_PROJECT", "GCP_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS",
    "ADMIN_TOKEN", "WORKER_TOKEN", "PORT", "HOST", "REDIS_URL",
    "PARTIALS", "WORKER_CONCURRENCY", "MAX_SEGMENT_MS", "SILENCE_MS",
    "MANIFEST", "DATA_DIR", "PUBLIC_URL",
]


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    for name in LEAKY:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"COTORRA_{name}", raising=False)
    # Point env_file at a path that does not exist, so a developer's .env in
    # the repo root cannot reach the tests.
    monkeypatch.setitem(Settings.model_config, "env_file", str(tmp_path / "absent.env"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
