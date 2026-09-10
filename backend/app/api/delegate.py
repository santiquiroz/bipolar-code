"""API del broker de delegación: enviar tareas a agentes CLI, seguir su log y cancelarlas."""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.models.delegate import JobRequest
from app.services.cli_agents import broker

router = APIRouter(prefix="/delegate", tags=["delegate"])


def _sse(event: dict) -> str:
    return f"event: {event.get('event', 'line')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/jobs")
async def submit_job(req: JobRequest, request: Request):
    try:
        job = await broker.submit(req, depth_header=request.headers.get("x-bipolar-depth", ""))
    except broker.WorkspaceNotAllowed as e:
        raise HTTPException(400, str(e))
    except broker.DelegationDisabled as e:
        raise HTTPException(409 if str(e) != "too_many_jobs" else 429, str(e))
    except broker.NoAgentAvailable as e:
        raise HTTPException(400, {"detail": "no_agent_available", "reasons": e.reasons, "skipped": e.skipped})
    return job.model_dump()


@router.get("/jobs")
async def list_jobs(limit: int = Query(50, ge=1, le=500), status: Optional[str] = None):
    return {"jobs": [j.model_dump() for j in broker.list_jobs(limit, status)]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = broker.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")
    return job.model_dump()


@router.get("/jobs/{job_id}/output", response_class=PlainTextResponse)
async def job_output(job_id: str):
    output = broker.job_output(job_id)
    if output is None:
        raise HTTPException(404, "Job no encontrado")
    return output


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    if broker.get_job(job_id) is None:
        raise HTTPException(404, "Job no encontrado")

    async def generate():
        async for event in broker.subscribe(job_id):
            yield _sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    job = await broker.cancel(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")
    return job.model_dump()
