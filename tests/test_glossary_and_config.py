from __future__ import annotations

import pytest

from cotorra.config import Settings, load_manifest
from cotorra.glossary import dedupe, glossary_path, list_glossaries, load_glossary, parse_glossary


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", manifest=tmp_path / "cotorra.yaml")
    s.ensure_dirs()
    return s


def write_glossary(settings: Settings, name: str, body: str) -> None:
    (settings.glossaries_dir / f"{name}.txt").write_text(body, encoding="utf-8")


# --- parsing ---------------------------------------------------------------


def test_comments_and_blank_lines_are_ignored():
    assert parse_glossary("# header\n\nKubernetes\n  eBPF  \n# trailing") == ["Kubernetes", "eBPF"]


def test_inline_comments_are_stripped():
    assert parse_glossary("gRPC # the RPC framework") == ["gRPC"]


def test_dedupe_is_case_insensitive_but_keeps_the_first_spelling():
    assert dedupe(["Kubernetes", "kubernetes", "eBPF"]) == ["Kubernetes", "eBPF"]


# --- loading ---------------------------------------------------------------


def test_default_glossary_applies_to_every_session(settings):
    write_glossary(settings, "default", "Nerdearla\nKonex")
    assert load_glossary(None, settings) == ["Nerdearla", "Konex"]


def test_a_named_glossary_is_merged_on_top_of_default(settings):
    write_glossary(settings, "default", "Kubernetes")
    write_glossary(settings, "keynote", "Sysarmy\nKubernetes")
    assert load_glossary("keynote", settings) == ["Kubernetes", "Sysarmy"]


def test_a_missing_glossary_is_not_fatal(settings):
    """A typo in cotorra.yaml must degrade accuracy, never stop the captions."""
    assert load_glossary("does-not-exist", settings) == []


def test_glossary_is_capped_so_it_cannot_dominate_the_prompt(settings):
    write_glossary(settings, "default", "\n".join(f"term{i}" for i in range(400)))
    assert len(load_glossary(None, settings)) == 150


def test_glossary_name_cannot_escape_the_glossary_directory(settings):
    """A session spec is operator input; treat it as untrusted anyway."""
    path = glossary_path("../../../../etc/passwd", settings)
    assert path.parent == settings.glossaries_dir


def test_listing_reports_term_counts(settings):
    write_glossary(settings, "default", "a\nb")
    write_glossary(settings, "stage1", "c")
    assert list_glossaries(settings) == {"default": 2, "stage1": 1}


# --- manifest --------------------------------------------------------------


def test_missing_manifest_is_an_empty_event(settings):
    assert load_manifest(settings.manifest) == []


def test_defaults_are_merged_into_every_session(tmp_path, monkeypatch):
    manifest = tmp_path / "cotorra.yaml"
    manifest.write_text(
        """
defaults:
  engine: mock
  targets: [es]
  glossary: shared
sessions:
  - id: one
    source: mic
  - id: two
    source: mic
    engine: gemini
""",
        encoding="utf-8",
    )
    specs = load_manifest(manifest)
    assert [s.id for s in specs] == ["one", "two"]
    assert specs[0].engine == "mock"
    assert specs[1].engine == "gemini"        # per-session wins
    assert all(s.glossary == "shared" for s in specs)


def test_env_vars_expand_with_a_default(tmp_path, monkeypatch):
    manifest = tmp_path / "cotorra.yaml"
    manifest.write_text(
        "sessions:\n  - id: one\n    source: mic\n    engine: ${MY_ENGINE:-mock}\n",
        encoding="utf-8",
    )
    assert load_manifest(manifest)[0].engine == "mock"
    monkeypatch.setenv("MY_ENGINE", "gemini")
    assert load_manifest(manifest)[0].engine == "gemini"


def test_relative_sources_resolve_against_the_repo_not_the_cwd(tmp_path):
    """``cotorra serve`` must behave the same from any working directory."""
    manifest = tmp_path / "cotorra.yaml"
    manifest.write_text(
        "sessions:\n  - id: one\n    source: ./samples/pipeline-test.ogg\n", encoding="utf-8"
    )
    source = load_manifest(manifest)[0].source
    assert source.endswith("pipeline-test.ogg")
    assert "samples" in source
    assert source != "./samples/pipeline-test.ogg"


def test_languages_lists_the_source_first(tmp_path):
    manifest = tmp_path / "cotorra.yaml"
    manifest.write_text(
        "sessions:\n  - id: one\n    source: mic\n    source_lang: en\n    targets: [es, pt]\n",
        encoding="utf-8",
    )
    assert load_manifest(manifest)[0].languages() == ["en", "es", "pt"]


def test_settings_accept_both_plain_and_prefixed_env_vars(monkeypatch):
    monkeypatch.setenv("COTORRA_ENGINE", "gemini")
    monkeypatch.setenv("PORT", "9000")
    s = Settings()
    assert s.engine == "gemini"
    assert s.port == 9000


def test_a_session_without_an_engine_follows_the_configured_default(tmp_path, monkeypatch):
    """Setting COTORRA_ENGINE in .env must actually reach the manifest.

    pydantic-settings reads .env into Settings, never into os.environ, so a
    ``${COTORRA_ENGINE}`` placeholder inside the YAML would quietly see
    nothing and every session would fall back to the wrong engine.
    """
    from cotorra.config import get_settings

    manifest = tmp_path / "cotorra.yaml"
    manifest.write_text(
        "sessions:\n"
        "  - id: inherits\n    source: mic\n"
        "  - id: explicit\n    source: mic\n    engine: mock\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COTORRA_ENGINE", "local")
    get_settings.cache_clear()
    try:
        specs = {s.id: s.engine for s in load_manifest(manifest)}
    finally:
        get_settings.cache_clear()

    assert specs["inherits"] == "local"
    assert specs["explicit"] == "mock"  # an explicit choice always wins
