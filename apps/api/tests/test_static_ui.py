from pathlib import Path


def test_static_dashboard_exposes_required_controls() -> None:
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text()
    for required in (
        "Show monthly revenue trend",
        "What is the return rate?",
        "Which products are best selling?",
        "Show my customer orders",
        "Generated SQL",
        "Retrieved schema context and safety status",
    ):
        assert required in page
