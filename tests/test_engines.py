"""Engine tests.

The Gemini tests never call the API: they exercise the prompt we send and the
parsing of what comes back, which is where the bugs actually live.
"""

from __future__ import annotations

import asyncio
import json
import types as pytypes

import pytest

from cotorra.config import Settings
from cotorra.engines import ENGINES, build_engine
from cotorra.engines.base import TranscribeRequest, TranscribeResult
from cotorra.engines.gemini import (
    GeminiEngine,
    _normalise_lang,
    _split_usage,
    _strip_fences,
    lang_name,
)
from cotorra.engines.mock import MockEngine

genai = pytest.importorskip("google.genai")


def req(**kw) -> TranscribeRequest:
    base = dict(
        pcm=b"\x00" * 32000,
        sample_rate=16000,
        source_lang="en",
        targets=["es", "pt"],
    )
    base.update(kw)
    return TranscribeRequest(**base)


# --- registry --------------------------------------------------------------


def test_every_engine_is_reachable_by_name():
    assert set(ENGINES) == {"gemini", "local", "mock"}


def test_unknown_engine_says_what_is_available():
    with pytest.raises(ValueError, match="Available: gemini, local, mock"):
        build_engine("whisper-turbo", Settings())


def test_gemini_without_a_key_fails_loudly_and_early():
    """Better to refuse at startup than to discover it mid-keynote."""
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_engine("gemini", Settings(gemini_api_key=""))


# --- prompt construction ---------------------------------------------------


def test_prompt_names_the_target_languages_by_code_and_name():
    prompt = GeminiEngine.build_prompt(req())
    assert "es, pt" in prompt
    assert "Spanish" in prompt and "Portuguese" in prompt


def test_prompt_asks_for_detection_when_the_language_is_unknown():
    assert "auto-detect" in GeminiEngine.build_prompt(req(source_lang="auto"))
    assert "English" in GeminiEngine.build_prompt(req(source_lang="en"))


def test_glossary_terms_are_included_and_capped():
    prompt = GeminiEngine.build_prompt(req(glossary=["Nerdearla", "eBPF"]))
    assert "Nerdearla" in prompt and "eBPF" in prompt

    # A runaway glossary must not blow up the prompt on every single call.
    huge = GeminiEngine.build_prompt(req(glossary=[f"term{i}" for i in range(500)]))
    assert "term119" in huge and "term120" not in huge


def test_context_is_labelled_as_do_not_repeat():
    """Without this the model happily re-transcribes the previous sentence."""
    prompt = GeminiEngine.build_prompt(req(context="We deploy with Kubernetes."))
    assert "do NOT repeat" in prompt
    assert "We deploy with Kubernetes." in prompt


def test_a_partial_tells_the_model_not_to_finish_the_sentence():
    assert "do not finish the sentence" in GeminiEngine.build_prompt(req(partial=True))
    assert "do not finish the sentence" not in GeminiEngine.build_prompt(req(partial=False))


# --- response parsing ------------------------------------------------------


def engine() -> GeminiEngine:
    return GeminiEngine(api_key="test-key", model="gemini-2.5-flash")


def fake_response(payload: dict, usage=None):
    return pytypes.SimpleNamespace(
        parsed=None, text=json.dumps(payload), usage_metadata=usage
    )


def test_parses_transcript_and_translations():
    result = engine()._to_result(
        fake_response({
            "source_language": "en",
            "transcript": "We deploy with Kubernetes.",
            "translations": [
                {"lang": "es", "text": "Lo desplegamos con Kubernetes."},
                {"lang": "pt", "text": "Nós implantamos com Kubernetes."},
            ],
        }),
        req(),
    )
    assert result.source_lang == "en"
    assert result.texts["en"] == "We deploy with Kubernetes."
    assert result.texts["es"] == "Lo desplegamos con Kubernetes."
    assert result.texts["pt"].startswith("Nós")


def test_a_translation_can_never_overwrite_the_verbatim_transcript():
    """If the model echoes the source language in translations, keep the original."""
    result = engine()._to_result(
        fake_response({
            "source_language": "en",
            "transcript": "The real words.",
            "translations": [{"lang": "en", "text": "A paraphrase."}],
        }),
        req(),
    )
    assert result.texts["en"] == "The real words."


def test_silence_produces_an_empty_result_not_a_hallucination():
    result = engine()._to_result(
        fake_response({"source_language": "en", "transcript": "", "translations": []}),
        req(),
    )
    assert result.is_empty


def test_markdown_fences_are_survived():
    resp = pytypes.SimpleNamespace(
        parsed=None,
        text='```json\n{"source_language":"en","transcript":"Hi","translations":[]}\n```',
        usage_metadata=None,
    )
    assert engine()._to_result(resp, req()).texts["en"] == "Hi"


def test_an_empty_response_does_not_crash_the_worker():
    resp = pytypes.SimpleNamespace(parsed=None, text="", usage_metadata=None)
    assert engine()._to_result(resp, req()).is_empty


@pytest.mark.parametrize(
    "given,expected",
    [("en", "en"), ("EN", "en"), ("es-AR", "es"), ("pt_BR", "pt"),
     ("Spanish", "es"), ("english", "en"), ("spa", "es"), ("", "")],
)
def test_language_codes_are_normalised(given, expected):
    assert _normalise_lang(given) == expected


def test_strip_fences_leaves_clean_json_alone():
    assert _strip_fences('{"a":1}') == '{"a":1}'


def test_lang_name_falls_back_to_the_code():
    assert lang_name("es").startswith("Spanish")
    assert lang_name("xx") == "xx"


# --- cost ------------------------------------------------------------------


def test_audio_and_text_tokens_are_billed_at_different_rates():
    """Lumping them together understates audio by roughly 3x."""
    usage = pytypes.SimpleNamespace(
        prompt_token_count=1000,
        prompt_tokens_details=[
            pytypes.SimpleNamespace(modality="AUDIO", token_count=800),
            pytypes.SimpleNamespace(modality="TEXT", token_count=200),
        ],
        candidates_token_count=120,
    )
    audio, text = _split_usage(usage, duration_s=25)
    assert (audio, text) == (800, 200)


def test_usage_falls_back_to_32_tokens_per_second_of_audio():
    audio, text = _split_usage(None, duration_s=5)
    assert (audio, text) == (160, 0)


def test_cost_uses_the_configured_rates():
    e = GeminiEngine(api_key="k", price_audio_in=1.0, price_text_in=0.3, price_text_out=2.5)
    cost = e.estimate_cost_usd(
        TranscribeResult(source_lang="en", texts={"en": "x"},
                         audio_tokens=1_000_000, input_tokens=0, output_tokens=0)
    )
    assert cost == pytest.approx(1.0)


# --- mock ------------------------------------------------------------------


async def test_mock_returns_every_requested_language():
    result = await MockEngine(latency_ms=0, jitter_ms=0).transcribe(req(targets=["es", "pt"]))
    assert {"es", "pt"} <= set(result.texts)
    assert not result.is_empty


async def test_mock_is_flagged_as_demo_so_no_one_mistakes_it_for_real():
    assert MockEngine().demo is True


async def test_a_mock_partial_is_a_prefix_of_its_final():
    m = MockEngine(latency_ms=0, jitter_ms=0)
    partial = await m.transcribe(req(partial=True))
    final = await m.transcribe(req(partial=False))
    assert final.texts["es"].startswith(partial.texts["es"])


async def test_mock_reports_a_plausible_cost():
    m = MockEngine(latency_ms=0, jitter_ms=0)
    result = await m.transcribe(req())
    assert 0 < m.estimate_cost_usd(result) < 0.01


# --- quota and rate limiting ----------------------------------------------
# Live captioning dies on 429s, not on bad transcription. These exercise the
# handling of the errors a free-tier key actually produces.

QUOTA_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
    "current quota... * Quota exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\n"
    "Please retry in 40.052438324s.', 'status': 'RESOURCE_EXHAUSTED', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '40s'}]}}"
)


def test_retry_delay_is_taken_from_what_the_api_asked_for():
    from cotorra.engines.gemini import _retry_delay_s

    assert _retry_delay_s(RuntimeError(QUOTA_429)) == pytest.approx(40.05, abs=0.1)


def test_retry_delay_falls_back_when_the_api_gives_no_hint():
    from cotorra.engines.gemini import _retry_delay_s

    assert _retry_delay_s(RuntimeError("429 RESOURCE_EXHAUSTED")) == 30.0


def test_non_rate_limit_errors_have_no_retry_delay():
    from cotorra.engines.gemini import _retry_delay_s

    assert _retry_delay_s(RuntimeError("503 UNAVAILABLE")) is None
    assert _retry_delay_s(RuntimeError("400 INVALID_ARGUMENT")) is None


def test_quota_errors_become_one_actionable_line():
    """The raw error is 1500 characters of JSON; an operator needs one sentence."""
    from cotorra.engines.gemini import _summarise

    summary = _summarise(RuntimeError(QUOTA_429))
    assert len(summary) < 200
    assert "20 requests/day" in summary
    assert "gemini-2.5-flash" in summary
    assert "billing" in summary.lower()


def test_transient_errors_say_they_are_transient():
    from cotorra.engines.gemini import _summarise

    assert "clears on its own" in _summarise(RuntimeError("503 UNAVAILABLE. blah"))


def test_thinking_config_is_dropped_once_a_model_rejects_it():
    """Flash-Lite models have no thinking mode and 400 on the parameter."""
    e = engine()
    assert "thinking_config" in e._cfg_kwargs

    assert e._drop_thinking() is True
    assert "thinking_config" not in e._cfg_kwargs

    # Idempotent: a second 400 must not send us round the loop again.
    assert e._drop_thinking() is False


async def test_a_hopeless_quota_wait_fails_fast_instead_of_queueing_stale_captions():
    from cotorra.engines.gemini import QuotaExceeded

    e = GeminiEngine(api_key="k", timeout_s=5, max_retries=3)

    async def always_quota(_contents, _budget=None):
        raise RuntimeError(QUOTA_429)  # asks us to wait 40 s

    e._call = always_quota
    with pytest.raises(QuotaExceeded, match="quota"):
        await e.transcribe(req())


async def test_an_utterance_is_abandoned_once_it_spends_its_whole_budget():
    """Captions publish in order, so a doomed retry holds up everything behind it."""
    from cotorra.engines.gemini import TooLate

    e = GeminiEngine(api_key="k", timeout_s=0.05, deadline_s=0.05, max_retries=3)

    async def times_out(_contents, _budget=None):
        raise asyncio.TimeoutError

    e._call = times_out
    with pytest.raises(TooLate, match="Gave up"):
        await e.transcribe(req())


async def test_the_deadline_shrinks_the_timeout_of_a_later_attempt():
    """The second attempt only gets whatever is left of the budget, not a fresh one."""
    e = GeminiEngine(api_key="k", timeout_s=30.0, deadline_s=1.0, max_retries=3)
    budgets = []

    async def record(_contents, budget=None):
        budgets.append(budget)
        raise RuntimeError("503 UNAVAILABLE")

    e._call = record
    with pytest.raises(RuntimeError):  # TooLate or the exhausted-retries error
        await e.transcribe(req())

    assert budgets[0] == pytest.approx(1.0, abs=0.2)
    assert budgets[1] < budgets[0]


def test_the_http_deadline_never_goes_below_what_the_api_accepts():
    """A short client-side budget must not produce a 400 from the transport.

    The API answers anything under 10 s with "Manually set deadline is too
    short", which used to fail every single call.
    """
    from cotorra.engines.gemini import MIN_HTTP_TIMEOUT_S

    e = GeminiEngine(api_key="k", timeout_s=3.0)
    http_timeout_ms = e._cfg_kwargs["http_options"].timeout
    assert http_timeout_ms >= MIN_HTTP_TIMEOUT_S * 1000
    # …while our own budget stays short, because that is the one that decides
    # whether a caption is still worth publishing.
    assert e.timeout_s == 3.0


# --- Vertex AI vs AI Studio -----------------------------------------------
# Same SDK, same everything downstream. The difference that matters to a user
# is that Google Cloud credits and per-minute quota live on the Vertex side.


def test_vertex_mode_needs_a_project_and_says_so():
    with pytest.raises(RuntimeError, match="GCP_PROJECT"):
        GeminiEngine(vertex=True, project="")


def test_no_credentials_at_all_points_at_both_ways_in():
    with pytest.raises(RuntimeError) as exc:
        GeminiEngine()
    assert "GEMINI_API_KEY" in str(exc.value)
    assert "VERTEX" in str(exc.value).upper()


def test_api_key_mode_is_the_default():
    assert GeminiEngine(api_key="k").vertex is False


def test_the_factory_passes_the_vertex_settings_through(monkeypatch):
    """A misconfigured factory would silently fall back to the free tier."""
    captured = {}

    import cotorra.engines.gemini as gem

    class Fake(gem.GeminiEngine):
        def __init__(self, **kw):
            captured.update(kw)
            # Skip the real client; we only care about what was handed over.

    monkeypatch.setattr(gem, "GeminiEngine", Fake)
    build_engine(
        "gemini",
        Settings(gemini_use_vertex=True, gcp_project="my-proj", gcp_location="europe-west4"),
    )
    assert captured["vertex"] is True
    assert captured["project"] == "my-proj"
    assert captured["location"] == "europe-west4"


def test_a_billing_error_says_what_to_check():
    """What the dashboard shows the moment a hackathon credit expires."""
    from cotorra.engines.gemini import _summarise

    msg = _summarise(RuntimeError(
        "403 PERMISSION_DENIED. {'error': {'message': 'This API method requires billing "
        "to be enabled. Please enable billing on project #x'}}"
    ))
    assert "Billing is not active" in msg
    assert "credit" in msg
