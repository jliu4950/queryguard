# Contributing

Use Python 3.9–3.13 from the project root. Before opening a change, run:

```bash
python -m pip install -e ".[dev]"
python -m app.seed
ruff format apps/api evals --check
ruff check apps/api evals
pytest -q
python evals/run_evals.py
```

Do not commit `.env` files, API keys, SQLite databases, caches, generated evaluation output, or build artifacts. Keep all test and evaluation cases on fictional demo data. CI uses `LLM_PROVIDER=demo`; tests for network adapters must use mocked transports.
