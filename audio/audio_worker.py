from __future__ import annotations

import sys
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Lock
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from audio.music_math import cents_difference, is_within_tolerance
from audio.pitch_detection import estimate_pitch


_WASAPI = "Windows WASAPI"
_INVALID_SAMPLE_RATE = -9997  # PortAudio: paInvalidSampleRate
_UNSUPPORTED_FORMAT = -2004287480  # WASAPI: AUDCLNT_E_UNSUPPORTED_FORMAT (0x88890008)

@contextmanager
def _audio_thread_com():
    if sys.platform != "win32":
        yield
        return

    import ctypes as ct

    # WASAPI는 오디오 작업 스레드에서 스트림이 닫힐 때까지 COM 초기화가 유지되어야 한다.
    # HRESULT를 직접 확인해 이미 다른 COM 모드인 스레드의 초기화 횟수를 변경하지 않는다.
    com = ct.WinDLL("ole32")
    com.CoInitializeEx.argtypes = (ct.c_void_p, ct.c_uint32)
    com.CoInitializeEx.restype = ct.c_int32
    com.CoUninitialize.restype = None
    initialized = com.CoInitializeEx(None, 0)
    try:
        yield
    finally:
        if initialized >= 0:
            com.CoUninitialize()


def _windows_enhancements_enabled(sd, device_index: int) -> bool | None:
    import ctypes as ct
    from uuid import UUID

    class PropertyKey(ct.Structure):
        _fields_ = [("format_id", ct.c_ubyte * 16), ("property_id", ct.c_uint32)]

    class PropertyData(ct.Union):
        # PROPVARIANT의 공용체는 두 포인터를 담을 수 있는 크기와 정렬이 필요하다.
        _fields_ = [("uint", ct.c_uint32), ("storage", ct.c_void_p * 2)]

    class PropVariant(ct.Structure):
        _fields_ = [("type", ct.c_ushort), ("reserved", ct.c_ushort * 3), ("data", PropertyData)]

    def com_method(interface, slot, restype, *argtypes):
        vtable = ct.cast(interface, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
        return ct.WINFUNCTYPE(restype, ct.c_void_p, *argtypes)(vtable[slot])

    try:
        # PortAudio에서 같은 장치 번호의 IMMDevice를 빌린다. 이름 비교로 다른 장치를 고르는 일을 피한다.
        library = ct.CDLL(sd._libname)
        get_endpoint = library.PaWasapi_GetIMMDevice
    except (AttributeError, OSError):
        return None
    get_endpoint.argtypes = (ct.c_int, ct.POINTER(ct.c_void_p))
    get_endpoint.restype = ct.c_int

    com = ct.WinDLL("ole32")
    com.PropVariantClear.argtypes = (ct.POINTER(PropVariant),)

    endpoint, store = ct.c_void_p(), ct.c_void_p()
    value = PropVariant()
    try:
        if get_endpoint(device_index, ct.byref(endpoint)) != 0 or not endpoint:
            return None
        # IMMDevice::OpenPropertyStore(STGM_READ) / IPropertyStore::GetValue의 COM 슬롯.
        open_store = com_method(endpoint, 4, ct.c_int32, ct.c_uint32, ct.POINTER(ct.c_void_p))
        if open_store(endpoint, 0, ct.byref(store)) < 0 or not store:
            return None
        key = PropertyKey((ct.c_ubyte * 16).from_buffer_copy(UUID("1da5d803-d492-4edd-8c23-e0c0ffee7f0e").bytes_le), 5)
        get_value = com_method(store, 5, ct.c_int32, ct.POINTER(PropertyKey), ct.POINTER(PropVariant))
        if get_value(store, ct.byref(key), ct.byref(value)) >= 0 and value.type == 19:  # VT_UI4
            return {0: True, 1: False}.get(value.data.uint)
        return None
    finally:
        com.PropVariantClear(ct.byref(value))
        if store:
            com_method(store, 2, ct.c_uint32)(store)  # IUnknown::Release. IMMDevice는 PortAudio 소유.


def _wasapi_raw_settings(sd):
    try:
        settings = sd.WasapiSettings()
        # sounddevice 공개 API에는 RAW가 없으므로 내부 접근을 이곳에 한정한다.
        settings._streaminfo.streamOption = 1  # PortAudio: eStreamOptionRaw
    except AttributeError:
        return None
    return settings


def _start_input_stream(sd, device, preferred_rate, block_size, callback, extra_settings):
    options = dict(device=device, blocksize=block_size, channels=1, dtype="float32", callback=callback)
    if extra_settings is not None:
        options["extra_settings"] = extra_settings

    def start(rate):
        stream = sd.InputStream(samplerate=rate, **options)
        try:
            stream.start()
        except sd.PortAudioError:
            stream.close()
            raise
        return stream

    try:
        return start(preferred_rate)
    except sd.PortAudioError as error:
        # WASAPI는 스트림 생성 시점이나 실제 시작 시점에 형식 오류를 반환할 수 있다.
        host_error = error.args[2] if len(error.args) > 2 else ()
        invalid_rate = error.args[1:2] == (_INVALID_SAMPLE_RATE,)
        unsupported_format = isinstance(host_error, tuple) and host_error[1:2] == (_UNSUPPORTED_FORMAT,)
        if sys.platform != "win32" or not (invalid_rate or unsupported_format):
            raise
        device_rate = sd.query_devices(device, "input")["default_samplerate"]
        if device_rate == preferred_rate:
            raise
        return start(device_rate)


def _open_windows_device(sd, device, sample_rate, block_size, callback):
    info = sd.query_devices(device, "input")
    host_api = sd.query_hostapis(info["hostapi"])["name"]
    if host_api == _WASAPI and _windows_enhancements_enabled(sd, info["index"]) is not False:
        raw = _wasapi_raw_settings(sd)
        if raw is not None:
            try:
                return _start_input_stream(sd, device, sample_rate, block_size, callback, raw)
            except sd.PortAudioError:
                # RAW 스트림을 열지 못해도 같은 마이크로 일반 입력을 시도한다.
                pass
    return _start_input_stream(sd, device, sample_rate, block_size, callback, None)


def _select_input_stream(sd, device, sample_rate, block_size, callback):
    if sys.platform != "win32":
        return _start_input_stream(sd, device, sample_rate, block_size, callback, None)
    if device is None:
        # PortAudio의 전체 기본 장치는 MME일 수 있다. Windows WASAPI 기본 입력을 먼저 시도한다.
        wasapi_default = next((api["default_input_device"] for api in sd.query_hostapis() if api["name"] == _WASAPI), -1)
        if wasapi_default >= 0 and wasapi_default != sd.default.device[0]:
            try:
                return _open_windows_device(sd, wasapi_default, sample_rate, block_size, callback)
            except sd.PortAudioError:
                pass
    return _open_windows_device(sd, device, sample_rate, block_size, callback)


@contextmanager
def open_input_stream(sd, device, sample_rate, block_size, callback):
    """오디오 작업 스레드에서 입력을 열고 같은 스레드에서 스트림과 COM을 정리한다."""
    with _audio_thread_com():
        stream = _select_input_stream(sd, device, sample_rate, block_size, callback)
        try:
            yield stream
        finally:
            try:
                stream.stop()
            finally:
                stream.close()


class AudioWorker(QThread):
    result_ready = Signal(dict)
    error_message = Signal(str)

    def __init__(
        self,
        expected_frequency: float,
        tolerance_cents: float = 30.0,
        sample_rate: int = 44100,
        block_size: int = 4096,
        min_freq: float = 70.0,
        max_freq: float = 1000.0,
        silence_threshold: float = 0.006,
        update_interval_ms: int = 50,
        result_hold_ms: int = 900,
        pitch_backend: str = "librosa_yin",
        device: int | str | None = None,
        target_id: int = 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if expected_frequency <= 0:
            raise ValueError("expected_frequency must be greater than 0 Hz.")
        if tolerance_cents < 0:
            raise ValueError("tolerance_cents must be greater than or equal to 0.")
        if result_hold_ms < 0:
            raise ValueError("result_hold_ms must be greater than or equal to 0.")

        self.expected_frequency = expected_frequency
        self.tolerance_cents = tolerance_cents
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.min_freq = min_freq
        self.max_freq = max_freq
        self.silence_threshold = silence_threshold
        self.update_interval_ms = update_interval_ms
        self.result_hold_ms = result_hold_ms
        self.pitch_backend = pitch_backend
        self.device = device
        self.target_id = target_id
        self._running = True
        self._target_lock = Lock()
        self._pending_target: tuple[float, int] | None = None

    def request_stop(self) -> None:
        self._running = False
        self.requestInterruption()

    def stop(self, timeout_ms: int = 2000) -> None:
        self.request_stop()
        if self.isRunning() and QThread.currentThread() is not self:
            self.wait(timeout_ms)

    def set_target(self, expected_frequency: float, target_id: int) -> None:
        if expected_frequency <= 0:
            raise ValueError("expected_frequency must be greater than 0 Hz.")
        with self._target_lock:
            self._pending_target = (expected_frequency, target_id)

    def _apply_pending_target(self) -> bool:
        with self._target_lock:
            pending = self._pending_target
            self._pending_target = None
        if pending is None:
            return False
        self.expected_frequency, self.target_id = pending
        return True

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depends on local environment
            self._emit_error_result(f"sounddevice import failed: {exc}")
            return
        if not self._running:
            return

        audio_blocks: Queue[np.ndarray] = Queue(maxsize=3)
        stream_status_message = ""

        def audio_callback(indata, _frames, _time_info, status) -> None:
            nonlocal stream_status_message
            if status:
                stream_status_message = str(status)
            block = np.asarray(indata[:, 0], dtype=np.float32).copy()
            try:
                audio_blocks.put_nowait(block)
            except Full:
                try:
                    audio_blocks.get_nowait()
                except Empty:
                    pass
                audio_blocks.put_nowait(block)

        self._apply_pending_target()
        self._emit(self._build_result(status="listening"))
        last_voiced_result: dict | None = None
        last_voiced_at = 0.0

        try:
            with open_input_stream(sd, self.device, self.sample_rate, self.block_size, audio_callback) as stream:
                sample_rate = int(stream.samplerate)
                next_emit_at = 0.0
                while self._running and not self.isInterruptionRequested():
                    if self._apply_pending_target():
                        _drain(audio_blocks)
                        last_voiced_result = None
                        next_emit_at = 0.0
                        self._emit(self._build_result(status="listening"))
                    try:
                        block = audio_blocks.get(timeout=0.1)
                    except Empty:
                        continue

                    now = perf_counter()
                    if now < next_emit_at:
                        continue

                    detection = estimate_pitch(
                        block,
                        sample_rate=sample_rate,
                        min_freq=self.min_freq,
                        max_freq=self.max_freq,
                        silence_threshold=self.silence_threshold,
                        backend=self.pitch_backend,
                    )
                    live_result = self._build_result(
                        detected_frequency=detection.frequency,
                        rms=detection.rms,
                        peak=detection.peak,
                        clarity=detection.clarity,
                        stream_status=stream_status_message,
                    )
                    if detection.frequency is not None:
                        last_voiced_result = live_result
                        last_voiced_at = now
                        self._emit(live_result)
                    elif (
                        last_voiced_result is not None
                        and (now - last_voiced_at) * 1000.0 <= self.result_hold_ms
                    ):
                        held_result = dict(last_voiced_result)
                        held_result["held"] = True
                        held_result["held_ms"] = int((now - last_voiced_at) * 1000.0)
                        held_result["stream_status"] = stream_status_message
                        self._emit(held_result)
                    else:
                        self._emit(live_result)
                    next_emit_at = now + self.update_interval_ms / 1000.0
        except Exception as exc:  # pragma: no cover - hardware and permission dependent
            self._emit_error_result(f"Microphone input failed: {exc}")
        finally:
            self._running = False

    def _emit(self, result: dict) -> None:
        result["target_id"] = self.target_id
        self.result_ready.emit(result)

    def _emit_error_result(self, message: str) -> None:
        self.error_message.emit(message)
        self._emit(self._build_result(status="error", message=message))

    def _build_result(
        self,
        detected_frequency: float | None = None,
        status: str | None = None,
        message: str = "",
        rms: float = 0.0,
        peak: float = 0.0,
        clarity: float = 0.0,
        stream_status: str = "",
        held: bool = False,
        held_ms: int = 0,
        pitch_backend: str | None = None,
    ) -> dict:
        cents = None
        is_correct = False

        if status is None:
            if detected_frequency is None:
                status = "no_signal"
            else:
                cents = cents_difference(detected_frequency, self.expected_frequency)
                is_correct = is_within_tolerance(
                    detected_frequency,
                    self.expected_frequency,
                    self.tolerance_cents,
                )
                status = "correct" if is_correct else "incorrect"

        return {
            "detected_frequency": detected_frequency,
            "expected_frequency": self.expected_frequency,
            "cents_difference": cents,
            "is_correct": is_correct,
            "status": status,
            "rms": rms,
            "peak": peak,
            "clarity": clarity,
            "message": message,
            "stream_status": stream_status,
            "held": held,
            "held_ms": held_ms,
            "pitch_backend": pitch_backend or self.pitch_backend,
        }


def _drain(blocks: Queue) -> None:
    while True:
        try:
            blocks.get_nowait()
        except Empty:
            return
