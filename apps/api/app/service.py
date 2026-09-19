from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Any, Optional, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .catalog import SchemaCatalog
from .config import Settings
from .providers import LLMProvider, provider_from_settings
from .schemas import QueryRequest, QueryResponse, Role, Trace
from .security import SafetyError, apply_role_filter, reject_prompt_injection, validate_sql

logger = logging.getLogger("queryguard.audit")


class Cache(Protocol):
    def get(self, key: str) -> Optional[QueryResponse]: ...

    def set(self, key: str, value: QueryResponse) -> None: ...


class TTLMemoryCache:
    """Process-local TTL cache. An external adapter can use this same small interface later."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._memory: dict[str, tuple[float, QueryResponse]] = {}

    def get(self, key: str) -> Optional[QueryResponse]:
        record = self._memory.get(key)
        if record is None:
            return None
        expires_at, value = record
        if monotonic() >= expires_at:
            del self._memory[key]
            return None
        return value

    def set(self, key: str, value: QueryResponse) -> None:
        self._memory[key] = (monotonic() + self.ttl_seconds, value)


@dataclass
class QueryService:
    engine: Engine
    settings: Settings
    catalog: SchemaCatalog
    provider: LLMProvider
    cache: Cache

    @classmethod
    def build(cls, engine: Engine, settings: Settings) -> "QueryService":
        return cls(
            engine=engine,
            settings=settings,
            catalog=SchemaCatalog(),
            provider=provider_from_settings(settings),
            cache=TTLMemoryCache(),
        )

    @staticmethod
    def _cache_key(request: QueryRequest) -> str:
        normalized = " ".join(request.question.lower().split())
        return hashlib.sha256(f"{normalized}|{request.role}|{request.user_id}".encode()).hexdigest()

    def query(self, request: QueryRequest) -> QueryResponse:
        started = perf_counter()
        cache_key = self._cache_key(request)
        cached = self.cache.get(cache_key)
        if cached:
            return cached.model_copy(
                update={"trace": cached.trace.model_copy(update={"cache_hit": True})}
            )
        reject_prompt_injection(request.question)
        context = self.catalog.retrieve(request.question)
        generated = self.provider.generate(request.question, request.role, context)
        if "UNSAFE_REFUSAL" in generated.sql:
            raise SafetyError("refused", "The demo provider cannot safely answer this question.")
        validated = validate_sql(
            generated.sql, self.catalog, request.role, self.settings.max_result_rows
        )
        executable_sql = apply_role_filter(validated, request.role, request.user_id)
        params = {"authorized_user_id": request.user_id} if request.role == Role.SALES_REP else {}
        with self.engine.connect() as connection:
            result = connection.execute(text(executable_sql), params)
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(self.settings.max_result_rows)]
        response = QueryResponse(
            answer_summary=_summary(rows, columns),
            sql=executable_sql,
            columns=columns,
            rows=_json_safe(rows),
            trace=Trace(
                schema_documents=[doc.as_trace() for doc in context],
                generation_ms=generated.generation_ms,
                cache_hit=False,
                safety={
                    "input": "passed",
                    "sql": "validated",
                    "authorization": "enforced_server_side",
                    "limit": self.settings.max_result_rows,
                    "pipeline_ms": round((perf_counter() - started) * 1000),
                },
            ),
        )
        self.cache.set(cache_key, response)
        logger.info(
            json.dumps(
                {
                    "event": "query_executed",
                    "question_digest": hashlib.sha256(request.question.encode()).hexdigest()[:12],
                    "role": request.role,
                    "user_id": request.user_id,
                    "tables": sorted(validated.tables),
                    "row_count": len(rows),
                }
            )
        )
        return response


def _summary(rows: list[list[Any]], columns: list[str]) -> str:
    if not rows:
        return "No matching demo records were found."
    return (
        f"Returned {len(rows)} row(s) with {len(columns)} field(s) from the fictional demo dataset."
    )


def _json_safe(rows: list[list[Any]]) -> list[list[Any]]:
    return [
        [
            float(value)
            if hasattr(value, "as_tuple")
            else value.isoformat()
            if hasattr(value, "isoformat")
            else value
            for value in row
        ]
        for row in rows
    ]
