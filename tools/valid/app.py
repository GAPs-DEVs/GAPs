import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.valid.valid_module import ValidLoader, ValidResultManager, ValidationApp


def main() -> None:
    # Step 1. 결과 폴더 선택
    # - 인자를 주면 그 폴더를 엽니다.
    # - 인자가 없으면 가장 최근 실행 결과를 엽니다.
    if len(sys.argv) >= 2:
        result_dir = Path(sys.argv[1]).expanduser().resolve()
    else:
        result_dir = ValidResultManager.latest_result_dir()

    # Step 2. 데이터셋과 결과를 다시 연결합니다.
    loader = ValidLoader()
    manager = ValidResultManager.from_existing(result_dir)

    # Step 3. 결과 확인 앱을 엽니다.
    app = ValidationApp(loader, manager)
    app.show()


if __name__ == "__main__":
    main()
