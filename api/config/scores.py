"""Create a private score from the desktop parser's flat note list."""
import json
from uuid import UUID, uuid4

from django.core.exceptions import RequestDataTooBig
from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .auth import authenticate_user, response


def integer(value, field, low=1, high=2147483647):
    # bool is an int subclass in Python, but is not a valid musical value.
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{field}: expected integer {low}..{high}")
    return value


def validate_score(payload):
    if not isinstance(payload, dict) or set(payload) - {"title", "source_type", "notes"}:
        raise ValueError("Only title, source_type and notes are accepted")
    title = payload.get("title")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or "\x00" in title:
        raise ValueError("title: expected 1..200 characters")
    source = payload.get("source_type", "import")
    if source not in ("guitar_pro", "image", "manual", "sample", "import"):
        raise ValueError("Invalid source_type")
    notes = payload.get("notes")
    if not isinstance(notes, list) or not 1 <= len(notes) <= 1000:
        raise ValueError("notes: expected 1..1000 notes")
    groups, seen = {}, set()
    for note in notes:
        if not isinstance(note, dict) or set(note) - {
                "step", "string", "fret", "midi_pitch", "duration_ms", "score_line", "measure", "beat"}:
            raise ValueError("Invalid note fields")
        step = integer(note.get("step"), "step")
        string = integer(note.get("string"), "string", 1, 6)
        fret = integer(note.get("fret"), "fret", 0, 30)
        midi = note.get("midi_pitch")
        if midi is not None:
            integer(midi, "midi_pitch", 0, 127)
        metadata = tuple(integer(note[key], key) if note.get(key) is not None else None
                         for key in ("score_line", "measure", "beat"))
        metadata += (integer(note.get("duration_ms", 500), "duration_ms"),)
        if (step, string) in seen:
            raise ValueError("Duplicate string in the same step")
        seen.add((step, string))
        if step in groups and groups[step]["metadata"] != metadata:
            raise ValueError("Notes in the same step must share timing and position")
        group = groups.setdefault(step, {"metadata": metadata, "notes": []})
        group["notes"].append((string, fret, midi))
    return title.strip(), source, groups


def save_score(user_id, title, source, groups):
    score_id, version_id = uuid4(), uuid4()
    note_count = sum(len(group["notes"]) for group in groups.values())
    # All five tables commit together. Any failure leaves no partial score.
    with transaction.atomic(), connection.cursor() as cursor:
        # Recheck and lock the account so disabling it cannot race this upload.
        cursor.execute("SELECT id FROM public.users WHERE id=%s AND status='active' "
                       "AND deleted_at IS NULL FOR UPDATE", [user_id])
        if cursor.fetchone() is None:
            raise PermissionError()
        cursor.execute("INSERT INTO public.scores(id, owner_user_id, title, visibility, source_type, parser_status) "
                       "VALUES (%s, %s, %s, 'private', %s, 'ready')", [score_id, user_id, title, source])
        cursor.execute("INSERT INTO public.score_versions(id, score_id, version_number, created_by_user_id, "
                       "note_count, step_count) VALUES (%s, %s, 1, %s, %s, %s)",
                       [version_id, score_id, user_id, note_count, len(groups)])
        step_rows, note_rows = [], []
        for sequence, group in sorted(groups.items()):
            step_id = uuid4()
            step_rows.append((step_id, version_id, sequence, *group["metadata"]))
            note_rows.extend((step_id, *note) for note in group["notes"])
        cursor.executemany("INSERT INTO public.score_steps(id, score_version_id, sequence_no, score_line, "
                           "measure_no, beat_no, duration_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)", step_rows)
        cursor.executemany("INSERT INTO public.score_notes(score_step_id,string_number,fret_number,midi_pitch) "
                           "VALUES (%s,%s,%s,%s)", note_rows)
        cursor.execute("INSERT INTO public.user_score_library(user_id,score_id,saved_score_version_id) "
                       "VALUES (%s,%s,%s)", [user_id, score_id, version_id])
    return {"id": str(score_id), "version_id": str(version_id), "version_number": 1,
            "title": title, "visibility": "private", "note_count": note_count, "step_count": len(groups)}


@require_POST
def upload_score(request):
    user = authenticate_user(request)
    if isinstance(user, JsonResponse):
        return user
    if request.content_type != "application/json":
        return response({"error": "json_required"}, 415)
    try:
        title, source, groups = validate_score(json.loads(request.body))
    except RequestDataTooBig:
        return response({"error": "request_too_large"}, 413)
    except (ValueError, UnicodeError) as exc:
        # Validation messages contain field names, never credentials or raw JSON.
        return response({"error": "invalid_score", "detail": str(exc)}, 400)
    try:
        score = save_score(user["id"], title, source, groups)
    except PermissionError:
        return response({"error": "account_disabled"}, 403)
    except DatabaseError:
        return response({"error": "database_unavailable"}, 503)
    return response({"score": score}, 201)


# Shared by both reads: a library entry is NOT an access grant. Unlisted link
# access is intentionally excluded until a link policy is implemented.
READ_ACCESS = """
    s.deleted_at IS NULL AND
    (s.owner_user_id = %(user_id)s OR s.visibility = 'public' OR
     EXISTS (SELECT 1 FROM public.score_members m
             WHERE m.score_id=s.id AND m.user_id=%(user_id)s))
"""
CURSOR_SALT = "guitar-practice.library-page.v1"


def library_page(user_id, after, limit):
    # Sort by immutable unique ID so title/favorite changes cannot shift offsets.
    # LATERAL gets each latest version without multiplying rows by note count.
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT s.id, s.title, s.description, s.visibility, s.parser_status,
                   s.updated_at, l.is_favorite, l.updated_at,
                   COALESCE(saved.id, latest.id), COALESCE(saved.version_number, latest.version_number),
                   latest.id, latest.version_number,
                   CASE WHEN s.owner_user_id=%(user_id)s THEN 'owner'
                        ELSE COALESCE((SELECT m.permission FROM public.score_members m
                                       WHERE m.score_id=s.id AND m.user_id=%(user_id)s), 'viewer') END
            FROM public.user_score_library l
            JOIN public.scores s ON s.id=l.score_id
            LEFT JOIN public.score_versions saved
                ON saved.id=l.saved_score_version_id AND saved.score_id=s.id
            LEFT JOIN LATERAL (
                SELECT v.id, v.version_number FROM public.score_versions v
                WHERE v.score_id=s.id ORDER BY v.version_number DESC LIMIT 1
            ) latest ON TRUE
            WHERE l.user_id=%(user_id)s AND """ + READ_ACCESS + """
              AND (%(after)s::uuid IS NULL OR s.id > %(after)s::uuid)
            ORDER BY s.id LIMIT %(limit)s
        """, {"user_id": user_id, "after": after, "limit": limit + 1})
        rows = cursor.fetchall()
    items = []
    for row in rows[:limit]:
        items.append({
            "score_id": str(row[0]), "title": row[1], "description": row[2],
            "visibility": row[3], "parser_status": row[4], "updated_at": row[5],
            "is_favorite": row[6], "library_updated_at": row[7],
            "selected_version_id": str(row[8]) if row[8] else None,
            "selected_version_number": row[9],
            "latest_version_id": str(row[10]) if row[10] else None,
            "latest_version_number": row[11],
            "has_update": row[8] is not None and row[10] is not None and row[8] != row[10],
            "permission": row[12],
        })
    return items, len(rows) > limit


@require_GET
def sync_scores(request):
    user = authenticate_user(request)
    if isinstance(user, JsonResponse):
        return user
    try:
        limit = int(request.GET.get("limit", "20"))
        if not 1 <= limit <= 100:
            raise ValueError()
        after = None
        if "cursor" in request.GET:
            token = request.GET["cursor"]
            if not token or len(token) > 2048:
                raise ValueError()
            data = signing.loads(token, salt=CURSOR_SALT, max_age=3600)
            if data["user_id"] != user["id"]:
                raise ValueError()
            after = str(UUID(data["after"]))
    except (ValueError, TypeError, KeyError, signing.BadSignature):
        return response({"error": "invalid_pagination"}, 400)
    try:
        items, more = library_page(user["id"], after, limit)
    except DatabaseError:
        return response({"error": "database_unavailable"}, 503)
    next_cursor = None
    if more and items:
        next_cursor = signing.dumps({"user_id": user["id"], "after": items[-1]["score_id"]}, salt=CURSOR_SALT)
    return response({"items": items, "next_cursor": next_cursor})


def version_content(user_id, score_id, version_id):
    # A single SQL statement checks access AND reads content in the same snapshot.
    # Never require a library entry here: owners, members and public readers may
    # request an authorized version directly. IDs must belong to the same score.
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT s.id, s.title, v.id, v.version_number, v.note_count, v.step_count,
                   COALESCE((
                       SELECT json_agg(json_build_object(
                           'score_step_id', st.id, 'step', st.sequence_no,
                           'score_line', st.score_line, 'measure', st.measure_no,
                           'beat', st.beat_no, 'duration_ms', st.duration_ms,
                           'string', n.string_number, 'fret', n.fret_number,
                           'midi_pitch', n.midi_pitch
                       ) ORDER BY st.sequence_no, n.string_number)
                       FROM public.score_steps st
                       JOIN public.score_notes n ON n.score_step_id=st.id
                       WHERE st.score_version_id=v.id
                   ), '[]'::json)
            FROM public.scores s JOIN public.score_versions v ON v.score_id=s.id
            WHERE s.id=%(score_id)s AND v.id=%(version_id)s AND """ + READ_ACCESS,
            {"user_id": user_id, "score_id": score_id, "version_id": version_id})
        row = cursor.fetchone()
    if row is None:
        return None
    # Django's PostgreSQL adapter may return JSON as text; handle either form.
    notes = json.loads(row[6]) if isinstance(row[6], str) else row[6]
    return {"score_id": str(row[0]), "title": row[1], "version_id": str(row[2]),
            "version_number": row[3], "note_count": row[4], "step_count": row[5], "notes": notes}


@require_GET
def score_version(request, score_id, version_id):
    user = authenticate_user(request)
    if isinstance(user, JsonResponse):
        return user
    try:
        content = version_content(user["id"], score_id, version_id)
    except DatabaseError:
        return response({"error": "database_unavailable"}, 503)
    # Do not disclose whether someone else's private score/version exists.
    if content is None:
        return response({"error": "score_version_not_found"}, 404)
    return response(content)
