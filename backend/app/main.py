import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import seed, service
from .config import get_settings
from .db import connect, init_db
from .routes import auth, operator, parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate()  # fail fast with a clear message
    init_db()
    db = connect()
    try:
        seed.run(db)
        recovered = service.recover_stuck_questions(db)
        if recovered:
            log.warning("Escalated %d question(s) that were interrupted by a restart", recovered)
    finally:
        db.close()
    yield


app = FastAPI(title="Little Acorns Front Desk", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
app.include_router(parent.router)
app.include_router(operator.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)  # no request bodies are logged
    return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)


@app.get("/api/health")
def health():
    return {"status": "ok"}
