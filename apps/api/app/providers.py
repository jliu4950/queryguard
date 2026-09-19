from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Optional, Protocol

import httpx
import sqlglot

from .catalog import RetrievedDocument
from .config import Settings
from .schemas import Role

BASE_SYSTEM_PROMPT = """You generate SQLite SQL for a fictional ecommerce demo. Return SQL only: no Markdown,
explanation, comments, or code fences. Use only approved tables and fields in the schema context. Produce exactly
one read-only SELECT or read-only CTE, use a reasonable LIMIT, and never use DDL, writes, transactions, or unknown
functions. Do not select customer email or other unneeded sensitive fields. For sales_rep requests, apply only
assigned-customer scope; the server will independently enforce that scope. If the request cannot be answered safely,
return exactly: SELECT 'UNSAFE_REFUSAL' AS refusal LIMIT 1"""

SQL_START_RE = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)
REFUSAL_RE = re.compile(r"\b(?:cannot|can't|unable|refuse|sorry)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GeneratedSQL:
    sql: str
    generation_ms: int


class ProviderError(RuntimeError):
    """A safe provider-facing error; it never contains an API key or raw upstream body."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class LLMProvider(Protocol):
    def generate(
        self, question: str, role: Role, context: list[RetrievedDocument]
    ) -> GeneratedSQL: ...


def build_system_prompt(role: Role, context: list[RetrievedDocument]) -> str:
    context_payload = [document.as_trace() for document in context]
    return (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        f"Request role: {role.value}\n"
        f"Schema context (JSON): {json.dumps(context_payload, ensure_ascii=False)}"
    )


class DemoLLMProvider:
    """Deterministic provider for local demos and CI; deliberately not a general language model."""

    def generate(self, question: str, role: Role, context: list[RetrievedDocument]) -> GeneratedSQL:
        start = perf_counter()
        q = question.lower()
        if any(
            term in q
            for term in (
                "ignore previous",
                "drop ",
                "delete ",
                "insert ",
                "update ",
                "password",
                "email",
            )
        ):
            sql = "SELECT 'UNSAFE_REFUSAL' AS refusal LIMIT 1"
        elif (
            any(term in q for term in ("monthly", "month", "trend", "revenue", "sales"))
            and "return" not in q
            and "product" not in q
        ):
            sql = "SELECT substr(CAST(ordered_at AS TEXT), 1, 7) AS month, ROUND(SUM(total_amount), 2) AS revenue FROM orders WHERE status = 'completed' GROUP BY 1 ORDER BY 1 LIMIT 24"
        elif "return" in q:
            sql = "SELECT ROUND(100.0 * COUNT(DISTINCT returns.order_id) / NULLIF(COUNT(DISTINCT orders.id), 0), 2) AS return_rate_pct FROM orders LEFT JOIN returns ON returns.order_id = orders.id LIMIT 1"
        elif any(
            term in q for term in ("top product", "hot product", "best selling", "销量", "热销")
        ):
            sql = "SELECT products.name AS product, SUM(order_items.quantity) AS units_sold FROM order_items JOIN products ON products.id = order_items.product_id JOIN orders ON orders.id = order_items.order_id WHERE orders.status = 'completed' GROUP BY products.name ORDER BY units_sold DESC LIMIT 10"
        elif any(term in q for term in ("my customer", "my order", "assigned", "客户订单", "订单")):
            sql = "SELECT customers.name AS customer, orders.id AS order_id, orders.total_amount, orders.status FROM orders JOIN customers ON customers.id = orders.customer_id ORDER BY orders.ordered_at DESC LIMIT 50"
        elif "customer" in q:
            sql = "SELECT customers.name AS customer, COUNT(orders.id) AS order_count, ROUND(SUM(orders.total_amount), 2) AS revenue FROM customers JOIN orders ON orders.customer_id = customers.id GROUP BY customers.name ORDER BY revenue DESC, customers.name ASC LIMIT 50"
        else:
            sql = "SELECT 'UNSAFE_REFUSAL' AS refusal LIMIT 1"
        return GeneratedSQL(sql=sql, generation_ms=round((perf_counter() - start) * 1000))


class OpenAICompatibleProvider:
    """Small chat-completions adapter, inactive unless explicitly configured."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ):
        if not base_url or not api_key or not model:
            raise ProviderError(
                "provider_configuration",
                "LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required for this provider.",
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def generate(self, question: str, role: Role, context: list[RetrievedDocument]) -> GeneratedSQL:
        started = perf_counter()
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": build_system_prompt(role, context)},
                {"role": "user", "content": question},
            ],
        }
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", "The configured LLM provider timed out."
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_unavailable", "The configured LLM provider is unavailable."
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(
                "provider_upstream_error", "The configured LLM provider returned an error."
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "provider_invalid_response",
                "The configured LLM provider returned non-JSON content.",
            ) from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "provider_invalid_response",
                "The configured LLM provider returned an unexpected response.",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                "provider_empty_response", "The configured LLM provider returned no SQL."
            )
        sql = content.strip()
        if REFUSAL_RE.search(sql) and not SQL_START_RE.match(sql):
            raise ProviderError(
                "provider_refusal", "The configured LLM provider declined the request."
            )
        if "```" in sql:
            raise ProviderError(
                "provider_non_sql_response", "The configured LLM provider did not return plain SQL."
            )
        try:
            statements = sqlglot.parse(sql, read="sqlite")
        except Exception as exc:
            raise ProviderError(
                "provider_non_sql_response", "The configured LLM provider did not return plain SQL."
            ) from exc
        if len(statements) != 1:
            raise ProviderError(
                "provider_non_sql_response", "The configured LLM provider did not return plain SQL."
            )
        return GeneratedSQL(sql=sql, generation_ms=round((perf_counter() - started) * 1000))


def provider_from_settings(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "demo":
        return DemoLLMProvider()
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
        )
    raise ProviderError("provider_configuration", "LLM_PROVIDER must be demo or openai_compatible.")
