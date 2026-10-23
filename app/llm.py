
import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from app.config import settings
from app.schemas import GeneratedPayload


SYSTEM_PROMPT = """You are a careful QA engineer for a fictional medical device manual.

Generate 3 to 5 concrete, executable software/device QA test-case ideas based ONLY on supplied text.

Do not invent numeric limits or requirements. If the text lacks an exact expected threshold, state that the expected result must be verified against the cited requirement.

Return ONLY valid JSON with this shape:
{
  "test_cases": [
    {
      "title": "...",
      "preconditions": "...",
      "steps": ["..."],
      "expected_result": "...",
      "requirement_refs": ["exact section heading"],
      "risk": "low|medium|high"
    }
  ],
  "assumptions": []
}
"""


class LLMError(RuntimeError):
    """Raised when LLM generation fails."""


def _extract_content(response: dict) -> str:
    """Extract generated content from an OpenAI-compatible response."""
    return response["choices"][0]["message"]["content"]


def generate_cases(
    context: str,
    retry_count: int | None = None,
) -> dict:
    """Generate and validate QA test cases using the configured LLM."""

    retries = (
        settings.max_llm_retries
        if retry_count is None
        else retry_count
    )

    if not settings.llm_api_key:
        raise LLMError(
            "LLM_API_KEY is not configured. Set it in .env; "
            "no fake test cases are returned."
        )

    prompt = (
        "Generate QA test cases using only the following manual text "
        "and headings. Cite the exact section headings provided.\n\n"
        + context
    )

    last_error = None

    for attempt in range(retries + 1):
        try:
            payload = {
                "model": settings.llm_model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            }

            body = json.dumps(payload).encode("utf-8")

            request = Request(
                url=(
                    settings.llm_base_url.rstrip("/")
                    + "/chat/completions"
                ),
                data=body,
                headers={
                    "Authorization": (
                        f"Bearer {settings.llm_api_key}"
                    ),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "tri9t-ai-assignment/1.0",
                },
                method="POST",
            )

            with urlopen(
                request,
                timeout=settings.llm_timeout_seconds,
            ) as response:
                raw = json.loads(
                    response.read().decode("utf-8")
                )

            content = _extract_content(raw)

            # Validate the model's output against the Pydantic schema.
            parsed = GeneratedPayload.model_validate_json(content)

            return parsed.model_dump()

        except HTTPError as exc:
            try:
                detail = exc.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                detail = str(exc)

            last_error = f"HTTP {exc.code}: {detail}"

        except (
            URLError,
            TimeoutError,
            KeyError,
            IndexError,
            ValueError,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        except Exception as exc:
            # Includes Pydantic validation errors.
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries:
            time.sleep(min(0.5 * (2 ** attempt), 2))

    raise LLMError(
        f"LLM generation or structured validation failed after "
        f"{retries + 1} attempt(s): {last_error}"
    )
