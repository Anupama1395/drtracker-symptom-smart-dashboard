# Shared test setup: tests never call a real LLM (fast, deterministic, works without Ollama).
# A test that wants LLM output patches parser.call_llm itself; that patch wins over this default.
import pytest

import parser


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    def offline(*_args, **_kwargs):
        raise ConnectionError("LLM disabled in tests")
    monkeypatch.setattr(parser, "call_llm", offline)
    monkeypatch.setenv("SYMPTOMLENS_LLM_CACHE", "")      # no shared LLM cache between tests
