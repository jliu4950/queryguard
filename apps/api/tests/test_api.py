from fastapi.testclient import TestClient

from app.main import app


def test_health_and_query_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "demo")
    from app.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.post(
            "/api/query",
            json={
                "question": "Which products are best selling?",
                "role": "analyst",
                "user_id": "demo_analyst",
            },
        )
        assert response.status_code == 200
        assert response.json()["trace"]["safety"]["sql"] == "validated"
    get_settings.cache_clear()
