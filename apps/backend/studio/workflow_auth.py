from secrets import compare_digest

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed


class ServiceIdentity:
    is_authenticated = True


class WorkflowServiceAuthentication(BaseAuthentication):
    def authenticate(self, request):
        return self.authenticate_token(request, settings.WORKFLOW_SERVICE_TOKEN, "workflow")

    @staticmethod
    def authenticate_token(request, token, identity):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if not token or len(parts) != 2 or parts[0].lower() != b"bearer" or not compare_digest(parts[1], token.encode()):
            raise AuthenticationFailed("Invalid service credential.")
        return ServiceIdentity(), identity

    def authenticate_header(self, request):
        return "Bearer"


class WorkflowVerifierAuthentication(WorkflowServiceAuthentication):
    def authenticate(self, request):
        token = settings.WORKFLOW_VERIFIER_TOKEN
        if token == settings.WORKFLOW_SERVICE_TOKEN:
            token = ""
        return self.authenticate_token(request, token, "workflow-verifier")


class WorkflowServiceScheme(OpenApiAuthenticationExtension):
    target_class = WorkflowServiceAuthentication
    name = "workflowService"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "n8n workflow service credential."}


class WorkflowVerifierScheme(OpenApiAuthenticationExtension):
    target_class = WorkflowVerifierAuthentication
    name = "workflowVerifier"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "Independent provider reconciliation credential."}
