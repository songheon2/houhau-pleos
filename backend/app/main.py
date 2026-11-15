from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import routers as feature_routers

app = FastAPI(title="호우하우 API", version="1.0.0")

for feature_router in feature_routers:
    app.include_router(feature_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """422 without echoing request values (coordinates, search terms)."""
    detail = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "houhau-api"}
