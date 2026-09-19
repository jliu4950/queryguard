import json

import httpx
import pytest

from app.catalog import SchemaCatalog
from app.config import Settings
from app.providers import OpenAICompatibleProvider, ProviderError, provider_from_settings
from app.schemas import Role


def make_provider(handler):
    return OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key=str(id(handler)),
        model="demo-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_openai_compatible_request_contains_context_role_and_sql_rules() -> None:
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "SELECT 1"}}]})

    result = make_provider(handler).generate(
        "Show monthly revenue", Role.SALES_REP, SchemaCatalog().retrieve("monthly revenue")
    )
    assert result.sql == "SELECT 1"
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["headers"]["authorization"].startswith("Bearer ")
    assert captured["payload"]["model"] == "demo-model"
    system_prompt = captured["payload"]["messages"][0]["content"]
    assert "Request role: sales_rep" in system_prompt
    assert "Schema context (JSON):" in system_prompt
    assert "Return SQL only" in system_prompt


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, content=b"not-json"), "provider_invalid_response"),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
            "provider_empty_response",
        ),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": "I cannot help"}}]}),
            "provider_refusal",
        ),
        (
            httpx.Response(
                200, json={"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}]}
            ),
            "provider_non_sql_response",
        ),
        (httpx.Response(500, text="upstream detail"), "provider_upstream_error"),
    ],
)
def test_openai_compatible_rejects_bad_responses(response, code) -> None:
    provider = make_provider(lambda _: response)
    with pytest.raises(ProviderError) as error:
        provider.generate("Show revenue", Role.ANALYST, [])
    assert error.value.code == code
    assert "upstream detail" not in str(error.value)


def test_openai_compatible_maps_timeouts_without_network() -> None:
    def handler(_: httpx.Request):
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(ProviderError) as error:
        make_provider(handler).generate("Show revenue", Role.ANALYST, [])
    assert error.value.code == "provider_timeout"


def test_provider_factory_uses_openai_adapter_only_when_explicitly_selected() -> None:
    provider = provider_from_settings(
        Settings(
            llm_provider="openai_compatible",
            llm_base_url="https://llm.example/v1",
            llm_api_key=str(id(Settings)),
            llm_model="demo-model",
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)
