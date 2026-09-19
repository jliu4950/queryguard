from app.catalog import SchemaCatalog


def test_retrieval_returns_relevant_schema() -> None:
    results = SchemaCatalog().retrieve("What is the return rate by month?")
    names = {result.name for result in results}
    assert {"returns", "orders"}.issubset(names)
