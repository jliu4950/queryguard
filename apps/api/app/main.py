import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Feedback, make_engine
from .providers import ProviderError
from .schemas import ErrorBody, FeedbackRequest, QueryRequest
from .security import SafetyError
from .seed import seed_database
from .service import QueryService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    seed_database(settings.database_url)
    app.state.service = QueryService.build(make_engine(settings.database_url), settings)
    yield


app = FastAPI(title="QueryGuard", version="0.1.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.exception_handler(SafetyError)
async def safety_error_handler(_: Request, exc: SafetyError):
    return JSONResponse(
        status_code=422, content={"error": ErrorBody(code=exc.code, message=str(exc)).model_dump()}
    )


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError):
    return JSONResponse(
        status_code=502,
        content={"error": ErrorBody(code=exc.code, message=str(exc)).model_dump()},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": ErrorBody(
                code="invalid_request",
                message="Request validation failed.",
                details={"issues": exc.errors()},
            ).model_dump()
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "provider": get_settings().llm_provider}


@app.get("/api/schema")
def schema() -> dict[str, object]:
    service: QueryService = app.state.service
    return {"version": service.catalog.version, "tables": service.catalog.tables}


@app.post("/api/query")
def query(request: QueryRequest):
    service: QueryService = app.state.service
    return service.query(request)


@app.post("/api/feedback", status_code=201)
def feedback(request: FeedbackRequest) -> dict[str, str]:
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        session.add(
            Feedback(
                question_digest=hashlib.sha256(request.question.encode()).hexdigest(),
                rating=request.rating,
                comment=request.comment,
            )
        )
        session.commit()
    return {"status": "recorded"}


@app.get("/api/evaluations/latest")
def latest_evaluations() -> dict[str, object]:
    latest = Path(__file__).resolve().parents[3] / "evals" / "latest.json"
    if latest.exists():
        return json.loads(latest.read_text())
    return {
        "status": "not_run",
        "message": "Run the versioned evaluation runner locally to generate demo-only metrics.",
    }
