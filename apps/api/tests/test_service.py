from pathlib import Path

import pytest

from app.catalog import SchemaCatalog
from app.config import Settings
from app.database import make_engine
from app.providers import DemoLLMProvider
from app.schemas import QueryRequest, Role
from app.security import SafetyError
from app.seed import seed_database
from app.service import QueryService, TTLMemoryCache


@pytest.fixture()
def service(tmp_path: Path) -> QueryService:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    seed_database(url)
    settings = Settings(database_url=url, max_result_rows=100)
    return QueryService(
        make_engine(url), settings, SchemaCatalog(), DemoLLMProvider(), TTLMemoryCache()
    )


def test_happy_path_and_cache(service: QueryService) -> None:
    request = QueryRequest(
        question="Show monthly revenue trend", role=Role.ANALYST, user_id="demo_analyst"
    )
    first = service.query(request)
    second = service.query(request)
    assert first.rows
    assert first.trace.cache_hit is False
    assert second.trace.cache_hit is True


def test_sales_rep_cannot_see_other_assigned_customer(service: QueryService) -> None:
    response = service.query(
        QueryRequest(question="Show my customer orders", role=Role.SALES_REP, user_id="rep_alex")
    )
    assert all(row[0] in {"Northstar Books", "Cedar Market"} for row in response.rows)
    assert "authorized_user_id" in response.sql


def test_irrelevant_question_refuses(service: QueryService) -> None:
    with pytest.raises(SafetyError) as error:
        service.query(
            QueryRequest(
                question="What is the weather tomorrow?", role=Role.ANALYST, user_id="demo_analyst"
            )
        )
    assert error.value.code == "refused"
