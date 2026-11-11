"""The 422 answer of every service that has no plain-language handler of its own (query has one)."""

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.utils.validation_messages import without_input


async def input_free_422(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 body, minus each error's rejected ``input``: callers log and relay these bodies."""
    return JSONResponse(status_code=422, content={"detail": without_input(jsonable_encoder(exc.errors()))})
