from fastapi import APIRouter

from app.services import copilot_catalog

router = APIRouter(tags=["copilot-compat"])


@router.get("/v1/bipolar/models")
async def bipolar_models() -> dict:
    return {"data": await copilot_catalog.build_catalog()}
