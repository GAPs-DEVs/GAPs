"""두 OS에서 같은 명령으로 Python, 네이티브 모듈, 모델 파일을 점검한다.

카메라나 마이크를 열지 않으므로 권한 요청 없이 실행할 수 있다.
장치의 실제 입력 품질은 앱에서 별도로 확인해야 한다.
"""
from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path
import platform
import sys
import tomllib


OPENCV_PACKAGES = (
    "opencv-python", "opencv-contrib-python",
    "opencv-python-headless", "opencv-contrib-python-headless",
)


def installed_opencv_packages() -> list[str]:
    """공유 cv2 파일이 덮어써질 수 있는 중복 배포판 설치를 찾는다."""
    installed = []
    for name in OPENCV_PACKAGES:
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        installed.append(name)
    return installed


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = []
    print(f"Python {platform.python_version()} | {platform.system()} {platform.machine()}")
    print(f"Executable: {sys.executable}")
    if not (3, 13) <= sys.version_info[:2] < (3, 15):
        failures.append("Use Python 3.13 or 3.14; the shared default is 3.14.")

    opencv = installed_opencv_packages()
    if opencv != ["opencv-contrib-python"]:
        failures.append(f"Expected only opencv-contrib-python; found {opencv}.")

    # 실제 바이너리 import까지 검사해야 DLL/dylib 로딩 오류도 확인할 수 있다.
    # 잠금 파일의 버전과 비교하여 OS별 임의 pip 설치로 생긴 차이도 알린다.
    with (root / "uv.lock").open("rb") as handle:
        locked = tomllib.load(handle)["package"]
    for distribution, module in (
        ("numpy", "numpy"), ("opencv-contrib-python", "cv2"),
        ("PySide6", "PySide6.QtWidgets"), ("mediapipe", "mediapipe"),
        ("torch", "torch"), ("ultralytics", "ultralytics"),
        ("librosa", "librosa"), ("sounddevice", "sounddevice"),
        ("pyguitarpro", "guitarpro"), ("google-auth-oauthlib", "google_auth_oauthlib"),
    ):
        try:
            importlib.import_module(module)
            version = metadata.version(distribution)
            expected = {p["version"] for p in locked if p["name"] == distribution.lower()}
            if version not in expected:
                raise RuntimeError(f"installed {version}, lock expects {sorted(expected)}")
            print(f"OK {distribution} {version}")
        except Exception as exc:
            failures.append(f"{distribution}: {exc}")

    for relative in ("models/best06.pt", "models/hand_landmarker.task", "examples/score_practice_sample.json"):
        if not (root / relative).is_file():
            failures.append(f"Missing resource: {relative}")

    for message in failures:
        print(f"FAIL {message}")
    print("Environment OK" if not failures else f"{len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
