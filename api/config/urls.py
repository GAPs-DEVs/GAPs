"""Read-only endpoints for process and database checks."""
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    # This endpoint works independently of database availability.
    return JsonResponse({"status": "ok"})


@require_GET
def database_health(request):
    # SELECT 1 checks authentication and query execution, not table permissions.
    # No user data is read and no schema is changed.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        # Never expose connection details or credentials in HTTP responses.
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [path("health/", health), path("health/db/", database_health)]
