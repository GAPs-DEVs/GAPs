"""OS별 카메라 백엔드 선택을 앱과 개발용 뷰어에서 공유한다."""
from __future__ import annotations

import sys

import cv2


def enumerate_camera_indices(max_index: int = 5) -> list[int]:
    """실제 프레임을 읽을 수 있는 장치만 반환하고 검사한 핸들은 항상 해제한다."""
    available = []
    for camera_index in range(max_index):
        capture = open_camera(camera_index)
        try:
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    available.append(camera_index)
        finally:
            capture.release()
    return available


def open_camera(camera_index: int):
    """기본 네이티브 백엔드를 우선하고, 열리지 않으면 대체 백엔드를 시도한다.

    실패한 핸들은 다음 시도 전에 해제한다. 반환된 핸들의 수명은 호출자가
    관리하며, 모두 실패하면 마지막 핸들의 isOpened()가 False를 반환한다.
    영상 파일이 아닌 실제 장치 인덱스에만 사용한다.
    """
    if sys.platform == "darwin":
        backends = (cv2.CAP_AVFOUNDATION, cv2.CAP_ANY)
    elif sys.platform == "win32":
        backends = (cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY)
    else:
        backends = (cv2.CAP_ANY,)

    for index, backend in enumerate(backends):
        capture = cv2.VideoCapture(camera_index, backend)
        if capture.isOpened() or index == len(backends) - 1:
            return capture
        capture.release()
