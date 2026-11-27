from django.db import OperationalError, connections
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from contracts.serializers import HealthSerializer


@extend_schema(responses=HealthSerializer)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
def health(request):
    return Response({"status": "ok"})


@extend_schema(responses={200: HealthSerializer, 503: HealthSerializer})
@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
def readiness(request):
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError:
        return Response({"status": "unavailable"}, status=503)
    return Response({"status": "ok"})
