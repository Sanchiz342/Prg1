from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from ..jobs import JobQueue
from ..services import webhooks
from .deps import get_queue, get_session

router = APIRouter(prefix="/api", tags=["webhooks"])


@router.post("/webhooks/github")
async def github_webhook(request: Request, x_github_event: str = Header(""), x_hub_signature_256: str | None = Header(None),
                         s: Session = Depends(get_session), queue: JobQueue = Depends(get_queue)):
    """Unauthenticated by design: GitHub cannot send a bearer token; the HMAC signature is the credential."""
    body = await request.body()
    result = webhooks.handle_github(s, queue, x_github_event, body, x_hub_signature_256)
    return {"deployments": result.deployments} if result.deployments else {"ignored": result.ignored}
