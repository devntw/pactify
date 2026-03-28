import logging
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routes.process import router as process_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="VSCE Layer 2",
    description="Verifiable Spoken Contract Engine — contract extraction layer",
    version="1.0.0",
)

app.include_router(process_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "layer2"}
