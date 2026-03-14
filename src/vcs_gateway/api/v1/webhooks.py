"""
Webhook ingestion endpoints — public surface for VCS providers.

Routes:
  POST /api/v1/webhooks/github/{tenant_id}
  POST /api/v1/webhooks/gitlab/{tenant_id}

The VcsGatewayService is instantiated once at startup and stored in
app.state.vcs_service (see main.py). Each endpoint just unpacks headers
and delegates to the service.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from vcs_gateway.services.vcs_gateway import (
    WebhookAccepted,
    WebhookDuplicate,
    WebhookIgnored,
    WebhookResult,
)

router = APIRouter(tags=["webhooks"])

_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB


def _check_body_size(raw_payload: bytes) -> JSONResponse | None:
    if len(raw_payload) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"code": "PAYLOAD_TOO_LARGE", "message": "Request body exceeds 5 MB limit"},
        )
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/webhooks/github/{tenant_id}")
async def receive_github_webhook(
    tenant_id: UUID,
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> JSONResponse:
    raw_payload = await request.body()
    if (err := _check_body_size(raw_payload)) is not None:
        return err
    result = await request.app.state.vcs_service.process_webhook(
        tenant_id=tenant_id,
        vcs_provider="github",
        raw_payload=raw_payload,
        headers={
            "X-Hub-Signature-256": x_hub_signature_256,
            "X-GitHub-Event": x_github_event,
        },
    )
    return _to_response(result)


@router.post("/webhooks/gitlab/{tenant_id}")
async def receive_gitlab_webhook(
    tenant_id: UUID,
    request: Request,
    x_gitlab_token: str = Header(default=""),
    x_gitlab_event: str = Header(default=""),
) -> JSONResponse:
    raw_payload = await request.body()
    if (err := _check_body_size(raw_payload)) is not None:
        return err
    result = await request.app.state.vcs_service.process_webhook(
        tenant_id=tenant_id,
        vcs_provider="gitlab",
        raw_payload=raw_payload,
        headers={
            "X-Gitlab-Token": x_gitlab_token,
            "X-Gitlab-Event": x_gitlab_event,
        },
    )
    return _to_response(result)


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------


def _to_response(result: WebhookResult) -> JSONResponse:
    if isinstance(result, WebhookAccepted):
        return JSONResponse(status_code=202, content=_accepted_body(result))
    if isinstance(result, WebhookDuplicate):
        return JSONResponse(status_code=200, content=_duplicate_body(result))
    return JSONResponse(status_code=200, content=_ignored_body(result))


def _accepted_body(result: WebhookAccepted) -> dict[str, Any]:
    return {
        "status": "accepted",
        "correlation_id": str(result.correlation_id),
        "event_id": str(result.event_id),
        "message": "Webhook accepted and scheduled for processing",
        "dispatch_in": "30s",
    }


def _duplicate_body(result: WebhookDuplicate) -> dict[str, Any]:
    return {
        "status": "ignored",
        "code": "DUPLICATE_WEBHOOK",
        "correlation_id": str(result.correlation_id),
        "pr_hash_key": result.pr_hash_key,
        "detection_method": result.detection_method,
    }


def _ignored_body(result: WebhookIgnored) -> dict[str, Any]:
    return {
        "status": "ignored",
        "code": result.code,
        "correlation_id": str(result.correlation_id),
        "event_type": result.event_type,
    }
