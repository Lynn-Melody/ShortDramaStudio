import json
from http.client import HTTPException, InvalidURL
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from django.utils import timezone

from scheduler.worker import DeliveryError, WorkflowRequest


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


webhooks = build_opener(ProxyHandler({}), RejectRedirects())


def dispatch_workflow(workflow: WorkflowRequest):
    remaining = (workflow.deadline_at - timezone.now()).total_seconds()
    if remaining <= 0:
        raise DeliveryError("workflow_deadline_elapsed", retryable=False)

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
    request = Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json",
        "Idempotency-Key": workflow.idempotency_key,
        "X-Studio-Dispatch-Token": settings.N8N_DISPATCH_TOKEN,
    }, method="POST")
    try:
        with webhooks.open(request, timeout=min(5, remaining)):
            pass
    except HTTPError as exc:
        exc.close()
        retryable = exc.code in (408, 425, 429) or 500 <= exc.code < 600
        raise DeliveryError(f"workflow_http_{exc.code}", retryable=retryable) from exc
    except (ValueError, InvalidURL) as exc:
        raise DeliveryError("workflow_webhook_invalid", retryable=False) from exc
    except (OSError, HTTPException) as exc:
        raise DeliveryError("workflow_transport_failed") from exc
