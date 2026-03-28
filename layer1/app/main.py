from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routes.ingest import router as ingest_router

app = FastAPI(title="VSCE Layer 1", version="1.0.0")
from app.routes.whatsapp import router as whatsapp_router

app.include_router(whatsapp_router, prefix="/webhook")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": jsonable_encoder(exc.errors()),
            "error_code": "validation_error",
        },
    )


app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
