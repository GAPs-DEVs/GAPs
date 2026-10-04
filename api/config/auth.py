"""Google identity verification and short-lived, server-signed service tokens."""
import json
import logging
from functools import partial

from django.conf import settings
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token

logger = logging.getLogger(__name__)
TOKEN_SALT = "guitar-practice.service-access.v1"


def member_from_google(claims):
    # Identify accounts by Google's immutable subject, never by email alone.
    subject = claims["sub"]
    email = claims.get("email")
    verified = claims.get("email_verified") is True
    name = str(claims.get("name") or "Google user").strip()[:80] or "Google user"
    with transaction.atomic(), connection.cursor() as cursor:
        # Serialize simultaneous first logins for the same Google account.
        # The unique provider/subject constraint remains the final DB safeguard.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ["google:" + subject])
        cursor.execute(
            "SELECT u.id, u.display_name, u.role, u.status, u.deleted_at "
            "FROM public.users u JOIN public.oauth_identities o ON o.user_id=u.id "
            "WHERE o.provider='google' AND o.provider_subject=%s FOR UPDATE OF u", [subject])
        row = cursor.fetchone()
        if row and (row[3] != "active" or row[4] is not None):
            raise PermissionError("Inactive account")
        if row is None:
            # Role defaults to 'user'; no role or owner ID is accepted from the app.
            cursor.execute(
                "INSERT INTO public.users(display_name) VALUES (%s) RETURNING id, display_name, role",
                [name])
            row = cursor.fetchone()
            cursor.execute(
                "INSERT INTO public.oauth_identities(user_id, provider, provider_subject) "
                "VALUES (%s, 'google', %s)", [row[0], subject])
        cursor.execute(
            "UPDATE public.oauth_identities SET email=%s, email_verified=%s, "
            "last_authenticated_at=CURRENT_TIMESTAMP WHERE provider='google' AND provider_subject=%s",
            [email, verified, subject])
        cursor.execute("UPDATE public.users SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s", [row[0]])
    return {"id": str(row[0]), "username": row[1], "role": row[2], "email": email, "is_guest": False}


def response(data, status=200):
    result = JsonResponse(data, status=status)
    result["Cache-Control"] = "no-store"
    return result


@require_POST
def google_login(request):
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        return response({"error": "google_login_not_configured"}, 503)
    if request.content_type != "application/json":
        return response({"error": "json_required"}, 415)
    try:
        payload = json.loads(request.body)
    except RequestDataTooBig:
        return response({"error": "request_too_large"}, 413)
    except (ValueError, UnicodeError):
        return response({"error": "invalid_json"}, 400)
    token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token or len(token) > 16384:
        return response({"error": "invalid_id_token"}, 400)
    try:
        # Google's library checks signature, issuer, audience and expiry.
        # Limit certificate-fetch time so an outage cannot occupy the worker forever.
        claims = verify_oauth2_token(token, partial(Request(), timeout=10), settings.GOOGLE_OAUTH_CLIENT_ID)
        if not isinstance(claims.get("sub"), str) or not 0 < len(claims["sub"]) <= 255:
            raise ValueError("Missing subject")
    except TransportError:
        return response({"error": "google_unavailable"}, 503)
    except (ValueError, GoogleAuthError):
        return response({"error": "invalid_google_token"}, 401)
    try:
        user = member_from_google(claims)
    except PermissionError:
        return response({"error": "account_disabled"}, 403)
    except DatabaseError:
        # Avoid logging query parameters, tokens or identity details.
        logger.error("Google login database operation failed")
        return response({"error": "database_unavailable"}, 503)
    access_token = signing.dumps({"user_id": user["id"]}, salt=TOKEN_SALT)
    return response({"access_token": access_token, "token_type": "Bearer",
                     "expires_in": settings.SERVICE_TOKEN_TTL, "user": user})


def authenticate_user(request):
    # A small authenticated endpoint verifies the complete login round trip.
    # Check current DB status on every request so suspended users are denied.
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or len(header) > 8192:
        return response({"error": "authentication_required"}, 401)
    try:
        data = signing.loads(header[7:], salt=TOKEN_SALT, max_age=settings.SERVICE_TOKEN_TTL)
        from uuid import UUID
        user_id = UUID(data["user_id"])
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        return response({"error": "invalid_or_expired_token"}, 401)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, display_name, role FROM public.users "
                           "WHERE id=%s AND status='active' AND deleted_at IS NULL", [user_id])
            row = cursor.fetchone()
    except DatabaseError:
        return response({"error": "database_unavailable"}, 503)
    if row is None:
        return response({"error": "account_disabled"}, 403)
    return {"id": str(row[0]), "username": row[1], "role": row[2]}


@require_GET
def current_user(request):
    user = authenticate_user(request)
    return user if isinstance(user, JsonResponse) else response({"user": user})
