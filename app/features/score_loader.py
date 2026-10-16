"""배포 예제와 사용자 캐시의 악보 목록을 화면 구성과 분리한다."""
from pathlib import Path

from app.config import DEFAULT_SCORE_PATH


def list_available_scores(cache_dir: str | Path, default_score: Path = DEFAULT_SCORE_PATH) -> list[tuple[str, str]]:
    """원본을 복사·삭제하지 않고 카드 제목과 실제 파일 경로를 반환한다."""
    entries = []
    if default_score.is_file():
        entries.append(("기본 연습 악보", str(default_score)))
    for directory in (default_score.parent, Path(cache_dir)):
        if not directory.is_dir():
            continue
        entries.extend(
            (path.name.removesuffix("_parsed.json"), str(path))
            for path in sorted(directory.glob("*_parsed.json")) if path.is_file()
        )
    return entries
