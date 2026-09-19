"""Runs versioned demo cases against the deterministic local pipeline."""

import json
from pathlib import Path
from time import perf_counter

from app.catalog import SchemaCatalog
from app.config import Settings
from app.database import make_engine
from app.providers import DemoLLMProvider
from app.schemas import QueryRequest
from app.security import SafetyError
from app.seed import seed_database
from app.service import QueryService, TTLMemoryCache


def percentile(values: list[float], percentage: float) -> float:
    return round(sorted(values)[min(len(values) - 1, int(len(values) * percentage))], 2)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{root / 'evals' / 'eval.db'}"
    seed_database(database_url)
    service = QueryService(
        make_engine(database_url),
        Settings(database_url=database_url),
        SchemaCatalog(),
        DemoLLMProvider(),
        TTLMemoryCache(),
    )
    cases = json.loads((root / "evals" / "cases.json").read_text())
    passed = 0
    security_intercepted = 0
    expected_refusals = sum(case["expect"] == "refused" for case in cases)
    authorization_violations = 0
    latencies: list[float] = []
    for case in cases:
        start = perf_counter()
        try:
            result = service.query(
                QueryRequest(
                    question=case["question"],
                    role=case["role"],
                    user_id=case["user_id"],
                )
            )
            success = (
                case["expect"] == "success"
                and result.columns == case["columns"]
                and result.rows == case["rows"]
            )
            if case["role"] == "sales_rep" and ":authorized_user_id" not in result.sql:
                authorization_violations += 1
        except SafetyError:
            security_intercepted += 1
            success = case["expect"] == "refused"
        latencies.append((perf_counter() - start) * 1000)
        passed += int(success)
    report = {
        "case_count": len(cases),
        "execution_accuracy": round(passed / len(cases), 3),
        "authorization_violations": authorization_violations,
        "safety_interception_rate": round(security_intercepted / expected_refusals, 3),
        "pipeline_latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "scope": "Metrics are generated only from this repository's deterministic fictional demo data.",
    }
    (root / "evals" / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
