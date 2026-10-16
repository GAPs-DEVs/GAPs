"""장치와 OS가 지원하는 마이크 입력 형식을 선택한다."""
from __future__ import annotations


def select_input_sample_rate(sounddevice, preferred_rate: float, device=None) -> float:
    """요청한 모노 float32 형식을 검사하고 필요하면 장치 기본 Hz를 사용한다.

    샘플레이트만 바꾸고 실제 입력 장치는 바꾸지 않는다. 선택한 장치가 없거나
    권한이 거부된 경우에는 오류를 호출자에게 전달한다. 반환값은 스트림뿐 아니라
    음정 분석과 화음 분석의 시간 창 계산에도 동일하게 사용해야 한다.
    """
    settings = dict(device=device, channels=1, dtype="float32")
    try:
        sounddevice.check_input_settings(samplerate=preferred_rate, **settings)
        return float(preferred_rate)
    except sounddevice.PortAudioError:
        # CoreAudio/WASAPI 장치가 요청한 44.1kHz를 지원하지 않는 경우를 처리한다.
        # 장치 조회 자체가 실패하면 다른 마이크로 바꾸지 않고 오류를 그대로 알린다.
        info = sounddevice.query_devices(device, "input")
        default_rate = float(info["default_samplerate"])
        sounddevice.check_input_settings(samplerate=default_rate, **settings)
        return default_rate
