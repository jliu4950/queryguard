# QueryGuard

[![CI](https://github.com/jliu4950/queryguard/actions/workflows/ci.yml/badge.svg)](https://github.com/jliu4950/queryguard/actions) [![Python 3.9 – 3.13](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.13-3776AB?logo=python&logoColor=white)](https://github.com/jliu4950/queryguard/actions) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**QueryGuard is a security-focused Text-to-SQL demo that turns questions about fictional ecommerce data into validated, authorized, read-only SQLite queries with an inspectable execution trace.**

This is an independent portfolio project. It does not use or reference company code, company data, customer data, or production metrics.

## Demo

A sales rep asks for "my customer orders". The rep never states *which* customers are
theirs, and the model is never asked to scope the query — the `customer_assignments`
predicate in the generated SQL is injected server-side after validation.

![Sales rep query with the server-injected authorization predicate visible in the generated SQL](docs/assets/demo-authorized-query.png)

The same pipeline stops a prompt-injection attempt during input validation, before any
SQL is generated:

![A prompt-injection question rejected with "The question contains a blocked instruction pattern"](docs/assets/demo-rejected-injection.png)

These images are build artifacts. Regenerate them against a running server with
`python scripts/capture_demo.py` (see [Development](#development)) rather than editing
them by hand.

## Architecture

Every request passes the same pipeline. The two shaded stages are the ones that make
the difference: the LLM is the only untrusted step, and everything after it is
deterministic server-side enforcement rather than prompt instruction.

```mermaid
flowchart TB
  U["Static dashboard / API client"] --> I["Input validation<br/><i>blocked instruction patterns</i>"]
  I --> R["Versioned schema catalog<br/><i>keyword / BM25 retrieval</i>"]
  R --> L["LLM provider<br/><i>demo or OpenAI-compatible</i>"]

  subgraph guard ["Untrusted output is validated, never trusted"]
    direction TB
    V["sqlglot SQL AST validation<br/><i>read-only allowlist</i>"]
    A["Row-level authorization<br/><i>server-injected predicate</i>"]
    V --> A
  end

  L -- "generated SQL" --> V
  A --> C["TTL memory cache"]
  C --> D[("SQLite fictional data")]
  D --> T["Answer + SQL + execution trace"]

  classDef untrusted fill:#fde68a,stroke:#b45309,color:#1f2937
  class L untrusted
```

## Key capabilities

- Runs from the repository root on Python 3.9–3.13; CI runs the same validation flow on both versions.
- Exposes a FastAPI API (`/health`, `/api/schema`, `/api/query`, `/api/feedback`, and `/api/evaluations/latest`) plus a lightweight static dashboard at `/`.
- Retrieves relevant tables, fields, joins, and business definitions from a versioned schema catalog using a transparent keyword/BM25-style baseline.
- Uses an LLM provider boundary to generate SQL, validates it as an AST with `sqlglot`, and returns the generated SQL, result rows, and a trace that includes retrieval context, cache status, and safety state.
- Executes against repeatable fictional ecommerce seed data in SQLite and caches normalized requests in a process-local TTL memory cache.

## Safety model

QueryGuard treats LLM output as untrusted input. It accepts one read-only `SELECT` or read-only CTE and rejects writes, DDL, transactions, comments, multi-statement SQL, dangerous functions, wildcard selection, sensitive fields, unknown catalog entities, and unsafe limits.

`sales_rep` access is enforced server-side by injecting an assignment predicate before execution. The demo identities `rep_alex` and `rep_sam` can only access their assigned customers, orders, and related order items. The request `role` and `user_id` fields are intentionally a simplified demo boundary; they are not a real authentication or identity system.

For `sales_rep`, authorization is enforced by appending one predicate to one `WHERE` clause, which is only sound when the statement has a single scope to constrain. CTEs, subqueries, and repeated table references are therefore refused for that role: each introduces a scope the predicate never reaches. Functions are checked against an allowlist, so an unrecognized function fails closed.

Structured audit events use a question digest rather than raw question text. See [docs/security.md](docs/security.md) for the detailed policy model.

## Quick start

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m app.seed
pytest -q
python evals/run_evals.py
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The SQLite seed setup is idempotent, and evaluation output is written locally to ignored `evals/latest.json`.

CI also runs:

```bash
ruff format apps/api evals scripts --check
ruff check apps/api evals scripts
```

## Development

The README screenshots are generated from a live server so they cannot drift from the
actual dashboard. With the server running:

```bash
python -m pip install -e ".[screenshots]"
python scripts/capture_demo.py --base-url http://127.0.0.1:8000
```

Playwright drives the locally installed Google Chrome, so no additional browser download
is needed. This is developer tooling only and is not part of the CI dependency set.

## API example

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H 'content-type: application/json' \
  -d '{"question":"Show my customer orders","role":"sales_rep","user_id":"rep_alex"}'
```

The response includes an answer summary, SQL, columns, rows, and a trace containing retrieved schema documents, generation time, cache state, and safety status.

## Providers

`DemoLLMProvider` is the default provider. It is deterministic, offline, and used by all local demos and CI. It supports a deliberately limited set of question families and is not intended to represent a general-purpose LLM.

An optional OpenAI-compatible chat-completions provider is enabled only when these environment variables are configured:

```bash
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-compatible-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=...
```

The adapter sends the request role, retrieved schema context, and SQL-only safety requirements in its system prompt. It returns clear errors for timeouts, non-JSON responses, empty responses, model refusals, and non-SQL output without exposing credentials or raw upstream response bodies. Its request construction and response parsing are covered by mocked offline tests. It has not been connected to a real external provider in this environment.

## Evaluation

Two suites, measuring different things.

### Correctness — `evals/run_evals.py`

The versioned evaluation suite contains 25 fictional demo cases. Successful cases compare expected columns and execution results; refusal cases compare explicit expected rejections. The suite does not compare SQL strings.

It reports execution accuracy, authorization violations, safety interception rate, and P50/P95 pipeline latency only after a local run. Those measurements apply only to this deterministic demo dataset and must not be interpreted as general model quality, production performance, or user-scale evidence.

### Security — `evals/run_adversarial.py`

The correctness suite cannot measure security: the demo provider never emits an attack, so
every control downstream of it is only ever exercised on benign input. The adversarial suite
assumes the opposite. For its `model_output` cases the provider is replaced by one that
returns attacker-chosen SQL, which is what a jailbroken or prompt-injected model effectively
gives you; the `question` cases leave the real provider in place and attack the input layer.

The pass criterion is **disclosure**, not refusal — refusing everything would score perfectly
and be useless. A case fails when a returned cell is something the requesting principal is not
entitled to see, judged against ground truth read from the database at run time rather than
from per-case snapshots. The runner exits non-zero on any failure, so CI fails on a real
regression.

44 cases across 42 techniques: 34 refused, 10 executed and contained, **0 disclosures**.

Writing it found six bypasses in controls this README already claimed to have — including
`SELECT *` disclosing every customer email, a CTE letting a sales rep read another rep's
customers, and a function blocklist that could never match the functions it named. All six
are fixed and pinned by regression tests. Replayed against the pre-fix code the same 44 cases
produce 5 disclosures, 1 query reaching the database, and 8 requirement failures.

Findings, root causes, and the gaps that remain:
[docs/adversarial-evaluation.md](docs/adversarial-evaluation.md).

## Verification results

Local verification was run with Python 3.13.9 and `LLM_PROVIDER=demo`:

- `ruff format apps/api evals --check` and `ruff check apps/api evals` passed.
- `pytest -q` passed: **36 tests passed**. The run emitted two upstream FastAPI/Starlette deprecation warnings.
- The 25-case evaluation completed with **execution accuracy 1.0**, **0 authorization violations**, **safety interception rate 1.0**, **P50 0.67 ms**, and **P95 1.54 ms**.
- The 44-case adversarial evaluation completed with **0 disclosures**, **0 queries reaching the database**, and **0 requirement failures** (34 refused, 10 executed and contained).
- Local API checks returned `200` for `/health`, analyst monthly revenue, and `rep_alex` assigned-customer orders. A prompt-injection request returned a structured `422` rejection, and the sales-rep SQL contained the server-side `customer_assignments` predicate.

These results are limited to the repository's fictional, deterministic demo data and the local verification environment. The optional real provider remains mock-tested only.

## Limitations and future work

- The demo provider covers a small, deterministic set of question types; it is not a general Text-to-SQL model.
- The input filter is a regex blocklist and is trivially evadable; the adversarial suite documents specific phrasings that get past it. It is a speed bump, not a boundary — authorization is what actually contains those requests.
- Rejecting every CTE and subquery for sales reps is sound but blunt: legitimate analytical queries are refused along with the attacks. A scope-aware rewrite that constrains every reference would be strictly better.
- Adversarial coverage is 42 hand-written techniques informed by reading the validator, which is biased toward bugs in code already under suspicion. It is not fuzzing and not a third-party red-team set.
- Identity is passed in the request and is not verified. There is no session handling, tenant isolation, or production policy-management system.
- The cache is process-local and clears on restart.
- Retrieval is keyword-based rather than semantic/vector retrieval.
- The SQL validator is intentionally conservative and rejects queries it cannot statically verify.
- Future work includes a Redis cache adapter, a PostgreSQL storage path with dialect-specific tests, verified identity claims and tenant boundaries, and optional containerization after separate runtime validation.

## Interview discussion points

1. Why row-level authorization must be a server-side query transformation rather than a prompt instruction.
2. Why a versioned schema allowlist plus SQL AST validation is more defensible than regex-only filtering.
3. How a deterministic provider makes demos, CI, and security regression tests reproducible without an API key.
4. Why a security suite has to judge disclosure rather than refusal, and how refusing everything would score perfectly while being useless.
5. Why the function blocklist could never fire, and why an allowlist is the only version of that control that fails closed.
6. Why surfacing retrieved schema context makes generated SQL easier to review and debug.
7. Why SQLite and a small cache interface are useful constraints for a portable, testable security demo before adding external infrastructure.
