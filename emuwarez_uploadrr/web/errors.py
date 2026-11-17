"""Error handlers and exception types for the web API."""
from __future__ import annotations

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


# Pydantic repite en cada error el valor recibido (`input`) y, a veces, la excepción que lo
# provocó (`ctx`). En esta API ese valor puede ser una URL con la clave en la query, una API
# key, un token o una contraseña: el 422 los devolvería a quien los escribió y quedarían en
# cualquier registro de respuestas. Se conservan `type`, `loc` y `msg`, que es lo que lee el
# frontend (api/client.ts).
_CAMPOS_QUE_REPITEN_EL_VALOR = ("input", "ctx")


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errores = [
        {k: v for k, v in error.items() if k not in _CAMPOS_QUE_REPITEN_EL_VALOR}
        for error in jsonable_encoder(exc.errors())
    ]
    return JSONResponse(status_code=422, content={"detail": errores})


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # El middleware de app.py ya deja la excepción en el log; su texto no sale de aquí.
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "Error interno: el detalle está en los logs",
                 "status_code": 500},
    )
