import asyncio
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.utils import timezone

from scheduler.worker import DeliveryError, WorkflowRequest


async def _post_webhook(url, payload, headers, timeout):
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(timeout=None, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                return response.status_code


def dispatch_workflow(workflow: WorkflowRequest):
    url = settings.N8N_WEBHOOKS.get(workflow.name)
    if not isinstance(url, str) or not url:
        raise DeliveryError("workflow_webhook_missing", retryable=False)
    try:
        target = urlsplit(url)
        if target.scheme not in {"http", "https"} or not target.hostname or target.username is not None or target.fragment:
            raise ValueError("Expected an HTTP(S) webhook URL without user credentials or a fragment")
        if target.port == 0:
            raise ValueError("Invalid port")
    except ValueError as exc:
        raise DeliveryError("workflow_webhook_invalid", retryable=False) from exc
    if not settings.N8N_DISPATCH_TOKEN:
        raise DeliveryError("workflow_dispatch_token_missing", retryable=False)

    payload = {
        "projectId": str(workflow.schedule_id),
        "taskId": str(workflow.task_id),
        "executionId": str(workflow.execution_id),
        "taskType": workflow.task_type,
        "schemaVersion": workflow.definition_version,
        "deadlineAt": workflow.deadline_at.isoformat(),
    }
    headers = {
        "Idempotency-Key": workflow.idempotency_key,
        "X-Studio-Dispatch-Token": settings.N8N_DISPATCH_TOKEN,
    }
    # Close the event loop without waiting on an OS DNS lookup after cancellation.
    loop = asyncio.new_event_loop()
    try:
        remaining = (workflow.deadline_at - timezone.now()).total_seconds()
        if remaining <= 0:
            raise DeliveryError("workflow_deadline_elapsed", retryable=False)
        status = loop.run_until_complete(_post_webhook(url, payload, headers, min(5, remaining)))
    except (httpx.InvalidURL, httpx.LocalProtocolError) as exc:
        raise DeliveryError("workflow_webhook_invalid", retryable=False) from exc
    except (TimeoutError, httpx.RequestError) as exc:
        raise DeliveryError("workflow_transport_failed") from exc
    finally:
        loop.close()
    if not 200 <= status < 300:
        retryable = status in (408, 425, 429) or 500 <= status < 600
        raise DeliveryError(f"workflow_http_{status}", retryable=retryable)
