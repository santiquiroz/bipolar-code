"""API de cuentas por CLI: listar, crear, borrar, elegir y reportar uso."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.accounts_service import (
    AccountError,
    create_account,
    delete_account,
    list_accounts,
    pick_account,
    record_usage,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])


class CreateAccountRequest(BaseModel):
    base: str
    label: str = ""


class UsageReport(BaseModel):
    rate_limits: dict = {}


@router.get("")
async def list_all():
    return {"accounts": list_accounts()}


@router.get("/pick")
async def pick(adapter: str, request: Request):
    out = pick_account(adapter)
    if out.get("mode") == "proxy":
        out["base_url"] = str(request.base_url).rstrip("/")
    return out


@router.post("")
async def create(req: CreateAccountRequest):
    try:
        agent, login = create_account(req.base, req.label)
    except AccountError as e:
        raise HTTPException(400, str(e))
    return {"agent": agent.model_dump(), "login": login}


@router.delete("/{agent_id}")
async def delete(agent_id: str):
    try:
        delete_account(agent_id)
    except AccountError as e:
        raise HTTPException(400, str(e))
    return {"deleted": agent_id}


@router.post("/{agent_id}/usage")
async def usage(agent_id: str, report: UsageReport):
    try:
        return record_usage(agent_id, report.rate_limits)
    except KeyError:
        raise HTTPException(404, f"cuenta no encontrada: {agent_id}")
