from fastapi import APIRouter
from app.services.pricing_service import get_all_prices, get_model_price, is_free

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.get("/models")
async def list_model_prices():
    return get_all_prices()


@router.get("/model/{provider_id}/{model_id:path}")
async def get_price(provider_id: str, model_id: str):
    price = get_model_price(provider_id, model_id)
    return {
        "provider_id": provider_id,
        "model": model_id,
        "price": price,
        "is_free": is_free(provider_id),
    }
