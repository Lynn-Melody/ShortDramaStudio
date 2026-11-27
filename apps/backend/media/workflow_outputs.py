from rest_framework.exceptions import APIException


class OutputRegistrationUnavailable(APIException):
    status_code = 503
    default_detail = "Workflow media output registration is unavailable."


def prepare_workflow_outputs(execution, output):
    """Validate declared objects before completion and return the transactional recorder."""
    if output["outputs"]:
        raise OutputRegistrationUnavailable()
    return None
