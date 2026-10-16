import os
import sys


def get_resource_path(relative_path: str) -> str:
    """개발 환경과 PyInstaller 번들 환경 모두에서 올바른 리소스 절대 경로 반환"""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 번들 실행 환경
        base_path = sys._MEIPASS
    else:
        # 일반 개발/실행 환경 (프로젝트 루트)
        base_path = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )  # 필요에 따라 루트 깊이 조정
    return os.path.join(base_path, relative_path)