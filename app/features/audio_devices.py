from __future__ import annotations


def enumerate_audio_input_devices() -> list[tuple[str, int]]:
    try:
        import sounddevice as sd
    except Exception:
        return []

    # Windows에서는 같은 마이크가 여러 API에 중복 노출된다.
    # 장치명만 표시하면 서로 구분할 수 없으므로 MME/WASAPI 등을 함께 표시한다.
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        hostapis = []

    try:
        devices = sd.query_devices()
    except Exception:
        return []

    input_devices: list[tuple[str, int]] = []
    for index, device in enumerate(devices):
        try:
            max_input_channels = int(device.get("max_input_channels", 0))
            name = str(device.get("name", f"Input {index}"))
        except AttributeError:
            max_input_channels = int(device["max_input_channels"])
            name = str(device["name"])

        if max_input_channels <= 0:
            continue
        try:
            host_index = int(device["hostapi"])
        except (KeyError, TypeError):
            host_index = -1
        host_name = str(hostapis[host_index]["name"]) if 0 <= host_index < len(hostapis) else ""
        prefix = f"[{host_name}] " if host_name else ""
        input_devices.append((f"{prefix}{name} ({max_input_channels}ch)", index))

    return input_devices
