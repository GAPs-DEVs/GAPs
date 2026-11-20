"""Resolve recording identity from the selected server cache, never a default song."""
import json
from pathlib import Path
from uuid import UUID


def resolve_practice_identity(client, path):
    path = Path(path)
    meta_path = path.with_name(path.name.replace("_parsed.json", ".meta.json"))
    if meta_path == path or not meta_path.exists():
        raise ValueError("서버에 등록된 악보를 보관함에서 선택해주세요.")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if metadata.get("cache_source") != "server":
        raise ValueError("서버 악보 정보를 확인할 수 없습니다.")
    score_id = str(UUID(metadata["score_id"]))
    # Older caches retain score_id but not version_id. Resolve it from sync.
    import requests
    cursor = None
    seen = set()
    version_id = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            f"{client.base_url}/api/scores/sync", params=params,
            headers=client._get_headers(), verify=False, timeout=5,
        )
        response.raise_for_status()
        page = response.json()
        for item in page.get("items", []):
            if item.get("score_id") == score_id:
                version_id = item.get("selected_version_id")
                break
        cursor = page.get("next_cursor")
        if version_id or not cursor or cursor in seen:
            break
        seen.add(cursor)
    if not version_id:
        raise ValueError("악보의 서버 버전을 확인할 수 없습니다. 악보함을 동기화해주세요.")
    version_id = str(UUID(version_id))
    response = requests.get(
        f"{client.base_url}/api/scores/{score_id}/versions/{version_id}",
        headers=client._get_headers(), verify=False, timeout=5,
    )
    response.raise_for_status()
    detail = response.json()
    if detail.get("score_id") != score_id or detail.get("version_id") != version_id:
        raise ValueError("선택한 악보와 서버 버전이 일치하지 않습니다.")
    server_notes = detail.get("notes")
    local = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(local, dict):
        local = local.get("notes", local.get("parsed_notes"))
    def signature(notes):
        return sorted((int(n["step"]), int(n["string"]), int(n["fret"])) for n in notes)
    if not isinstance(server_notes, list) or not server_notes or signature(server_notes) != signature(local):
        raise ValueError("로컬 악보와 서버 악보가 다릅니다. 동기화 후 다시 선택해주세요.")
    step_ids = {}
    for note in server_notes:
        step = int(note["step"])
        step_id = str(UUID(note["score_step_id"]))
        if step in step_ids and step_ids[step] != step_id:
            raise ValueError("서버 단계 ID가 일치하지 않습니다.")
        step_ids[step] = step_id
    return version_id, step_ids
