"""Create a private score from the desktop parser's flat note list."""
import json
from uuid import uuid4

from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
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
