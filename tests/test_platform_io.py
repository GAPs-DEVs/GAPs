"""실제 카메라·마이크 없이 OS별 장치 선택과 샘플레이트 대체를 검증한다."""
from unittest.mock import MagicMock, Mock
from types import SimpleNamespace
import sys

import cv2
import numpy as np
import pytest

from audio.input_settings import select_input_sample_rate
from vision import camera_capture


@pytest.mark.parametrize(
    "platform, backend",
    [("win32", cv2.CAP_MSMF), ("darwin", cv2.CAP_AVFOUNDATION), ("linux", cv2.CAP_ANY)],
)
def test_camera_uses_native_backend(monkeypatch, platform, backend):
    monkeypatch.setattr(camera_capture.sys, "platform", platform)
    camera = Mock()
    camera.isOpened.return_value = True
    factory = Mock(return_value=camera)
    monkeypatch.setattr(camera_capture.cv2, "VideoCapture", factory)
    assert camera_capture.open_camera(2) is camera
    factory.assert_called_once_with(2, backend)
    camera.release.assert_not_called()


def test_windows_camera_falls_back_and_releases_failed_handles(monkeypatch):
    monkeypatch.setattr(camera_capture.sys, "platform", "win32")
    failed, working = Mock(), Mock()
    failed.isOpened.return_value = False
    working.isOpened.return_value = True
    factory = Mock(side_effect=[failed, working])
    monkeypatch.setattr(camera_capture.cv2, "VideoCapture", factory)
    assert camera_capture.open_camera(0) is working
    failed.release.assert_called_once()
    assert factory.call_args.args == (0, cv2.CAP_DSHOW)
    working.release.assert_not_called()


def test_all_macos_camera_failures_return_closed_handle(monkeypatch):
    monkeypatch.setattr(camera_capture.sys, "platform", "darwin")
    cameras = [Mock(), Mock()]
    for camera in cameras:
        camera.isOpened.return_value = False
    monkeypatch.setattr(camera_capture.cv2, "VideoCapture", Mock(side_effect=cameras))
    assert camera_capture.open_camera(0) is cameras[-1]
    cameras[0].release.assert_called_once()
    # 마지막 핸들은 호출자의 finally에서 해제하므로 여기서는 유지한다.
    cameras[-1].release.assert_not_called()


class InputFormatError(Exception):
    pass


def test_microphone_keeps_supported_requested_rate():
    sd = Mock(PortAudioError=InputFormatError)
    assert select_input_sample_rate(sd, 44100, device=3) == 44100
    sd.query_devices.assert_not_called()
    sd.check_input_settings.assert_called_once_with(
        samplerate=44100, device=3, channels=1, dtype="float32"
    )


def test_microphone_falls_back_to_selected_device_rate():
    sd = Mock(PortAudioError=InputFormatError)
    sd.check_input_settings.side_effect = [InputFormatError("unsupported rate"), None]
    sd.query_devices.return_value = {"default_samplerate": 48000}
    assert select_input_sample_rate(sd, 44100, device=3) == 48000
    sd.query_devices.assert_called_once_with(3, "input")
    assert sd.check_input_settings.call_args.kwargs == dict(
        samplerate=48000, device=3, channels=1, dtype="float32"
    )


def test_microphone_failure_is_reported_without_changing_devices():
    sd = Mock(PortAudioError=InputFormatError)
    sd.check_input_settings.side_effect = InputFormatError("unavailable")
    sd.query_devices.side_effect = InputFormatError("device permission denied")
    with pytest.raises(InputFormatError, match="permission denied"):
        select_input_sample_rate(sd, 44100, device=3)
    sd.query_devices.assert_called_once_with(3, "input")


@pytest.mark.parametrize("mode", ["single", "chord"])
def test_workers_use_fallback_rate_for_stream_and_analysis(monkeypatch, mode):
    """장치만 48kHz로 열고 검출기는 44.1kHz로 계산하는 회귀를 방지한다."""
    from audio import audio_worker, chord_audio_worker
    from audio.polyphonic_detection import ExpectedFrequency

    sd = Mock(PortAudioError=InputFormatError)
    sd.check_input_settings.side_effect = [InputFormatError("unsupported rate"), None]
    sd.query_devices.return_value = {"default_samplerate": 48000}
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    if mode == "single":
        worker = audio_worker.AudioWorker(440., device=3)
        module, detector_name = audio_worker, "estimate_pitch"
    else:
        worker = chord_audio_worker.ChordAudioWorker([ExpectedFrequency("A4", 440.)], device=3)
        module, detector_name = chord_audio_worker, "detect_expected_frequencies_cqt"

    input_levels = []
    worker.input_level_ready.connect(input_levels.append)

    def detect(*_args, **_kwargs):
        # 검출기가 아직 실행되지 않은 시점에도 입력 크기가 이미 전달돼야 한다.
        # 분석 결과는 무음으로 만들어 실제 입력 표시와 독립적인지도 검사한다.
        assert input_levels and input_levels[0] == pytest.approx(0.02)
        # 첫 분석 뒤 종료하여 실제 스레드나 마이크 없이 run() 전체를 검사한다.
        worker._running = False
        if mode == "single":
            return SimpleNamespace(frequency=None, rms=0., peak=0., clarity=0.)
        return SimpleNamespace(matches=(), rms=0., peak=0., noise_floor_db=-120.,
                               is_correct=False, status="no_signal", backend="librosa_cqt")

    detector = Mock(side_effect=detect)
    monkeypatch.setattr(module, detector_name, detector)

    def feed_blocks():
        callback = sd.InputStream.call_args.kwargs["callback"]
        block = np.full((worker.block_size, 1), 0.02, dtype=np.float32)
        callback(block, len(block), None, None)
        callback(block, len(block), None, None)

    # 컨텍스트 관리자 매직 메서드를 제공하는 별도 mock을 사용한다.
    sd.InputStream.return_value = MagicMock()
    sd.InputStream.return_value.__enter__.side_effect = feed_blocks
    errors = []
    worker.error_message.connect(errors.append)
    worker.run()
    assert errors == []
    assert sd.InputStream.call_args.kwargs["samplerate"] == 48000
    assert detector.call_args.kwargs["sample_rate"] == 48000
