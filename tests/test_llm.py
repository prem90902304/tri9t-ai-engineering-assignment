import json
import pytest

from app import llm
from app.config import settings


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _valid_content() -> str:
    return json.dumps({
        "test_cases": [
            {"title": f"Check requirement behavior {i}",
             "preconditions": "The device is powered on",
             "steps": ["Start a measurement", "Observe the device response"],
             "expected_result": "The observed behavior matches the cited manual requirement",
             "requirement_refs": ["1 Safety"], "risk": "medium"}
            for i in range(1, 4)
        ],
        "assumptions": [],
    })


def test_llm_retries_malformed_structured_output(monkeypatch):
    responses = iter([
        FakeResponse(_completion('{"test_cases": []}')),
        FakeResponse(_completion(_valid_content())),
    ])
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "max_llm_retries", 1)
    monkeypatch.setattr(llm, "urlopen", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    result = llm.generate_cases("[1 Safety] Use the device according to instructions.")
    assert len(result["test_cases"]) == 3
    assert result["test_cases"][0]["title"] == "Check requirement behavior 1"


def test_llm_does_not_return_fake_output_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    with pytest.raises(llm.LLMError, match="LLM_API_KEY is not configured"):
        llm.generate_cases("[1 Safety] Use the device according to instructions.")


def test_llm_fails_after_retry_budget_for_invalid_schema(monkeypatch):
    responses = iter([
        FakeResponse(_completion('{"test_cases": []}')),
        FakeResponse(_completion('{"test_cases": []}')),
    ])
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "max_llm_retries", 1)
    monkeypatch.setattr(llm, "urlopen", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    with pytest.raises(llm.LLMError, match=r"after 2 attempt\(s\)"):
        llm.generate_cases("[1 Safety] Use the device according to instructions.")
