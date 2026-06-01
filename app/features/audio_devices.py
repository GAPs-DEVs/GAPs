from __future__ import annotations


def enumerate_audio_input_devices() -> list[tuple[str, int]]:
    try:
        import sounddevice as sd
    except Exception:
        return []

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
        input_devices.append((f"{name} ({max_input_channels}ch)", index))

    return input_devices
