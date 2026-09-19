import json
import re
from dataclasses import dataclass
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[3] / "packages" / "schema-catalog" / "catalog.json"
TOKEN_RE = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class RetrievedDocument:
    name: str
    score: float
    description: str
    columns: dict[str, str]
    joins: list[str]

    def as_trace(self) -> dict[str, object]:
        return {
            "table": self.name,
            "score": round(self.score, 3),
            "description": self.description,
            "columns": self.columns,
            "joins": self.joins,
        }


class SchemaCatalog:
    """Transparent keyword/BM25-style baseline; replaceable through this interface."""

    def __init__(self, path: Path = CATALOG_PATH):
        raw = json.loads(path.read_text())
        self.version: str = raw["version"]
        self.tables: dict[str, dict[str, object]] = {
            table["name"]: table for table in raw["tables"]
        }

    def retrieve(self, question: str, limit: int = 4) -> list[RetrievedDocument]:
        query_tokens = set(TOKEN_RE.findall(question.lower()))
        results: list[RetrievedDocument] = []
        for name, table in self.tables.items():
            text = " ".join(
                [
                    name,
                    str(table["description"]),
                    *table["columns"].keys(),
                    *table["columns"].values(),
                    *table["joins"],
                ]
            ).lower()
            tokens = TOKEN_RE.findall(text)
            # A small, inspectable term-frequency score is sufficient for the demo catalog.
            score = sum(tokens.count(token) / (1 + len(tokens) ** 0.5) for token in query_tokens)
            if name.rstrip("s") in question.lower():
                score += 2
            results.append(
                RetrievedDocument(
                    name,
                    score,
                    str(table["description"]),
                    dict(table["columns"]),
                    list(table["joins"]),
                )
            )
        ranked = sorted(results, key=lambda item: (-item.score, item.name))
        selected = ranked[:limit]
        selected_names = {document.name for document in selected}
        by_name = {document.name: document for document in ranked}

        # The generator needs join partners as well as lexical matches. Preserve a directly related
        # catalog table by replacing the lowest-ranked non-anchor document when the context is full.
        for anchor in tuple(selected):
            related_names = {
                token.split(".")[0]
                for join in anchor.joins
                for token in join.replace("=", " ").split()
                if token.split(".")[0] in self.tables
            }
            for related_name in sorted(related_names):
                if related_name in selected_names:
                    continue
                if len(selected) >= limit:
                    eviction = next(
                        (
                            document
                            for document in reversed(selected)
                            if document.name != anchor.name
                        ),
                        None,
                    )
                    if eviction is None:
                        continue
                    selected.remove(eviction)
                    selected_names.remove(eviction.name)
                selected.append(by_name[related_name])
                selected_names.add(related_name)
        return sorted(selected, key=lambda item: (-item.score, item.name))

    @property
    def allowed_tables(self) -> set[str]:
        return set(self.tables)

    def allowed_columns(self, table: str) -> set[str]:
        return set(self.tables[table]["columns"])
