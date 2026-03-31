from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from src.types import SQLGenerationOutput, AnswerGenerationOutput

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-4.1-nano"
DEFAULT_FALLBACK_MODELS = "google/gemini-2.0-flash-lite:free"

_TRANSIENT_ERROR_SUBSTRINGS = (
    "rate limit",
    "timeout",
    "502",
    "503",
    "529",
    "overloaded",
)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class OpenRouterLLMClient:
    provider_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        fallback_models: list[str] | None = None,
    ) -> None:
        try:
            from openrouter import OpenRouter
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install 'openrouter'.") from exc
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        if fallback_models is not None:
            self._fallback_models = fallback_models
        else:
            env_val = os.getenv("OPENROUTER_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
            self._fallback_models = [m.strip() for m in env_val.split(",") if m.strip()]
        self._client = OpenRouter(api_key=api_key)
        self._last_model_used: str = self.model
        self._stats: dict[str, int] = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def _try_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str,
    ) -> str:
        """Send to a single model with exponential-backoff retries on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                res = self._client.chat.send(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )
                break
            except Exception as exc:
                last_exc = exc
                err_lower = str(exc).lower()
                if attempt < MAX_RETRIES and any(s in err_lower for s in _TRANSIENT_ERROR_SUBSTRINGS):
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM call attempt %d/%d on %s failed (%s), retrying in %.1fs",
                        attempt,
                        MAX_RETRIES,
                        model,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]

        self._last_model_used = model
        self._stats["llm_calls"] += 1
        usage = getattr(res, "usage", None)
        if usage is not None:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            tt = getattr(usage, "total_tokens", 0) or 0
            self._stats["prompt_tokens"] += int(pt)
            self._stats["completion_tokens"] += int(ct)
            self._stats["total_tokens"] += int(tt)
        else:
            logger.warning("Token usage not returned by API; skipping token count for this call")

        choices = getattr(res, "choices", None) or []
        if not choices:
            raise RuntimeError("OpenRouter response contained no choices.")

        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None) if msg else None

        if isinstance(content, str):
            return content.strip()

        # Reasoning models (e.g. gpt-5-nano) may put output in the reasoning field
        reasoning = getattr(msg, "reasoning", None) if msg else None
        if isinstance(reasoning, str) and reasoning.strip():
            logger.warning("Model returned reasoning but no content; extracting from reasoning field")
            return reasoning.strip()

        raise RuntimeError("OpenRouter response contained no extractable text (content and reasoning both empty).")

    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        """Try the primary model, then each fallback in order."""
        models = [self.model] + list(self._fallback_models)
        last_exc: Exception | None = None
        for i, model_name in enumerate(models):
            try:
                return self._try_chat(messages, temperature, max_tokens, model_name)
            except Exception as exc:
                last_exc = exc
                if i < len(models) - 1:
                    logger.warning(
                        "Model %s failed (%s), falling back to %s",
                        model_name,
                        exc,
                        models[i + 1],
                    )
                continue
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _extract_sql(text: str) -> str | None:
        """Returns None for unanswerable (sql=null) or unparseable responses."""
        maybe_json = text.strip()

        if maybe_json.startswith("```"):
            lines = maybe_json.split("\n")
            inner_lines = []
            inside = False
            for line in lines:
                if line.strip().startswith("```") and not inside:
                    inside = True
                    continue
                elif line.strip() == "```" and inside:
                    break
                elif inside:
                    inner_lines.append(line)
            if inner_lines:
                maybe_json = "\n".join(inner_lines).strip()

        if maybe_json.startswith("{"):
            end = maybe_json.rfind("}")
            if end != -1:
                candidate = maybe_json[: end + 1]
                try:
                    parsed = json.loads(candidate)
                    sql = parsed.get("sql")
                    if sql is None:
                        return None
                    if isinstance(sql, str) and sql.strip():
                        return sql.strip().rstrip(";").strip()
                    return None
                except json.JSONDecodeError:
                    pass

        lower = text.lower()
        idx = lower.find("select ")
        if idx >= 0:
            raw = text[idx:].strip()
            raw = raw.split(";")[0].strip()
            raw = raw.split("```")[0].strip()
            return raw if raw else None
        return None

    def generate_sql(self, question: str, schema_text: str) -> SQLGenerationOutput:
        system_prompt = (
            "You are a SQLite SQL generator for an analytics pipeline.\n\n"
            f"{schema_text}\n\n"
            "Rules:\n"
            "- Generate ONLY SELECT queries. Never generate DELETE, UPDATE, INSERT, DROP, or ALTER.\n"
            "- Use ONLY the columns listed above. Do not invent columns.\n"
            "- If the question cannot be answered using the available columns, "
            'respond with: {"sql": null, "reason": "cannot answer from available schema"}\n'
            "- Always include a LIMIT clause (max 100) unless the query uses aggregation.\n"
            '- Respond ONLY with JSON: {"sql": "SELECT ..."}\n'
            "- No explanations, no markdown, just the JSON object."
        )

        start = time.perf_counter()
        error = None
        sql = None

        try:
            text = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.0,
                max_tokens=256,
            )
            logger.debug("LLM SQL response: %s", text[:300])
            sql = self._extract_sql(text)
        except Exception as exc:
            error = str(exc)
            logger.error("SQL generation failed: %s", error)

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self._last_model_used

        return SQLGenerationOutput(
            sql=sql,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def generate_answer(self, question: str, sql: str | None, rows: list[dict[str, Any]]) -> AnswerGenerationOutput:
        empty_stats = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "model": self._last_model_used,
        }

        if not sql:
            return AnswerGenerationOutput(
                answer=(
                    "I cannot answer this question with the available table and schema."
                    " The data does not contain the required information."
                ),
                timing_ms=0.0,
                llm_stats=empty_stats,
                error=None,
            )
        if not rows:
            return AnswerGenerationOutput(
                answer="The query executed successfully but returned no matching rows.",
                timing_ms=0.0,
                llm_stats=empty_stats,
                error=None,
            )

        truncated_rows = rows[:20]
        system_prompt = (
            "You are a concise data analyst. Answer based ONLY on the provided query results. "
            "Do not invent data. Be specific with numbers."
        )
        user_prompt = (
            f"Question: {question}\n\n"
            f"SQL: {sql}\n\n"
            f"Results ({len(rows)} rows):\n{json.dumps(truncated_rows, ensure_ascii=True)}\n\n"
            "Provide a concise answer in plain English."
        )

        start = time.perf_counter()
        error = None
        answer = ""

        try:
            answer = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=512,
            )
        except Exception as exc:
            error = str(exc)
            answer = f"Error generating answer: {error}"
            logger.error("Answer generation failed: %s", error)

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self._last_model_used

        return AnswerGenerationOutput(
            answer=answer,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def pop_stats(self) -> dict[str, Any]:
        out = dict(self._stats)
        self._stats = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        return out


def build_default_llm_client() -> OpenRouterLLMClient:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)
