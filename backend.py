"""Background worker: owns all hardware access so the UI thread never blocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from corsair_hid import CorsairVoidElite, HeadsetStatus, SIDETONE_MAX
from win_audio import AudioState, HeadsetAudio, com_init, com_uninit

AUDIO_POLL_MS = 3_000
HEADSET_POLL_EVERY = 3  # The headset is read once every N audio polls.
BLACK = (0, 0, 0)


@dataclass
class Snapshot:
    headset: HeadsetStatus = field(default_factory=HeadsetStatus)
    firmware: Optional[str] = None
    audio: AudioState = field(default_factory=AudioState)


def _hex_to_rgb(text: str) -> tuple[int, int, int]:
    value = text.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class Worker(QObject):
    snapshot = Signal(object)
    error = Signal(str)

    def __init__(self, sidetone_percent, lights_on, colors) -> None:
        super().__init__()
        self._sidetone_percent = sidetone_percent
        self._lights_on = lights_on
        self._colors: Optional[list[str]] = colors  # None: built-in multicolour effect
        self._state = Snapshot()
        self._tick = 0
        self._device = CorsairVoidElite()
        self._audio: Optional[HeadsetAudio] = None
        self._timer: Optional[QTimer] = None
        self._was_connected = False

    @Slot()
    def start(self) -> None:
        com_init()
        self._audio = HeadsetAudio()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(AUDIO_POLL_MS)
        self.refresh()

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._device.close()
        if self._audio is not None:
            self._audio.close()
        com_uninit()
        # Called directly: the UI thread is blocked waiting for this thread to end.
        QThread.currentThread().quit()

    def _poll(self) -> None:
        self._tick += 1
        if self._tick % HEADSET_POLL_EVERY == 0:
            self._read_headset()
        self._read_audio()
        self.snapshot.emit(self._state)

    @Slot()
    def refresh(self) -> None:
        self._read_headset()
        self._read_audio()
        self.snapshot.emit(self._state)

    def _read_audio(self) -> None:
        try:
            self._state.audio = self._audio.read()
        except OSError as exc:
            self._state.audio = AudioState()
            self.error.emit(str(exc))

    def _read_headset(self) -> None:
        status = self._device.read_status()
        self._state.headset = status
        if status.connected and not self._was_connected:
            # Sidetone and LED state are lost when the headset powers off.
            self._state.firmware = self._device.read_firmware()
            self._restore_headset()
        self._was_connected = status.connected

    def _restore_headset(self) -> None:
        try:
            if self._sidetone_percent is not None:
                self._device.set_sidetone(self._sidetone_level(self._sidetone_percent))
            self._sync_lighting()
        except (RuntimeError, ValueError) as exc:
            self.error.emit(str(exc))

    def _led_colors(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        if self._lights_on is False:
            return BLACK, BLACK
        left, right = self._colors[0], self._colors[-1]
        return _hex_to_rgb(left), _hex_to_rgb(right)

    def _sync_lighting(self, use_builtin_effect: bool = False) -> None:
        """Bring the LEDs to the wanted state.

        The built-in effect is only forced on request: otherwise a headset the
        user never customised here keeps whatever it is doing.
        """
        if self._lights_on is False or self._colors is not None:
            self._device.set_lighting_mode(hardware=False)
            self._send_leds()
        elif use_builtin_effect:
            self._device.set_lighting_mode(hardware=True)

    def _send_leds(self) -> None:
        self._device.set_leds(*self._led_colors())

    @staticmethod
    def _sidetone_level(percent: int) -> int:
        return round(max(0, min(100, percent)) * SIDETONE_MAX / 100)

    def _attempt(self, action, *args) -> None:
        try:
            action(*args)
        except (OSError, RuntimeError, ValueError) as exc:
            self.error.emit(str(exc))
        else:
            self._read_audio()
            self.snapshot.emit(self._state)

    @Slot(float)
    def set_output_volume(self, level: float) -> None:
        self._attempt(self._audio.set_output_volume, level)

    @Slot(bool)
    def set_output_muted(self, state: bool) -> None:
        self._attempt(self._audio.set_output_muted, state)

    @Slot(float)
    def set_mic_volume(self, level: float) -> None:
        self._attempt(self._audio.set_mic_volume, level)

    @Slot(bool)
    def set_mic_muted(self, state: bool) -> None:
        self._attempt(self._audio.set_mic_muted, state)

    @Slot(int)
    def set_sidetone(self, percent: int) -> None:
        self._sidetone_percent = percent
        self._attempt(self._device.set_sidetone, self._sidetone_level(percent))

    @Slot(bool)
    def set_lights(self, enabled: bool) -> None:
        self._lights_on = enabled
        self._attempt(self._sync_lighting, enabled)

    @Slot(int)
    def play_alert(self, sound_id: int) -> None:
        self._attempt(self._device.play_alert, sound_id)

    @Slot(object)
    def set_colors(self, colors) -> None:
        """Apply custom colours, or go back to the built-in effect when None."""
        previous, self._colors = self._colors, colors
        if colors is not None and previous is not None and self._lights_on is not False:
            # Already in software mode: this path runs for every slider movement.
            self._attempt(self._send_leds)
        else:
            self._attempt(self._sync_lighting, colors is None)
