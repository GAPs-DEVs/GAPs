"""Library removal and cleanup of an identified server cache."""
import json
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QThread

from app.api_client import api_client


class RemoveScoreThread(QThread):
    def __init__(self, score_id, cache_path, cache_dir, parent=None):
        super().__init__(parent)
        self.score_id = score_id
        self.cache_path = Path(cache_path)
        self.cache_dir = Path(cache_dir)
        self.success = False
        self.message = ""

    def run(self):
        try:
            score_id = str(UUID(str(self.score_id)))
            expected = self.cache_dir / "server" / f"{score_id}_parsed.json"
            metadata_path = expected.parent / f"{score_id}.meta.json"
            if (self.cache_path != expected or expected.parent.is_symlink() or self.cache_path.is_symlink()
                    or metadata_path.is_symlink()):
                raise ValueError("서버 캐시 경로를 확인할 수 없습니다.")
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (data.get("cache_source") != "server" or data.get("score_id") != score_id
                    or data.get("is_default") is True):
                raise ValueError("삭제 가능한 개인 악보인지 확인할 수 없습니다.")
            self.success, self.message = api_client.remove_library_score(score_id)
            if self.success:
                try:
                    self.cache_path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                except OSError:
                    self.message = "서버 악보함에서 제거했습니다. 로컬 캐시 정리에 실패하여 목록에 남아 있을 수 있습니다."
        except (OSError, ValueError, TypeError, AttributeError) as error:
            self.message = str(error)
