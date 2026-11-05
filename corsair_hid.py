"""Direct HID access for Corsair VOID ELITE Wireless receivers.

Protocol references:
- Linux kernel drivers/hid/hid-corsair-void.c (status, firmware, sidetone, alert)
- HeadsetControl lib/devices/corsair_void_rich.hpp (interfaces, LED report)
- Colour report: observed from iCUE's writes and tested on a VOID ELITE Wireless

Not thread-safe: use one instance from a single thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import hid  # hidapi Python bindings (package: hidapi)
except ImportError:  # Keep the UI importable when the dependency is missing.
    hid = None

CORSAIR_VID = 0x1B1C
# VOID ELITE Wireless IDs listed by the Linux kernel HID driver.
VOID_ELITE_PIDS = (0x0A51, 0x0A55, 0x0A75)

USAGE_PAGE_CONTROL = 0xFFC5  # status, alert, LED on/off
USAGE_PAGE_SIDETONE = 0xFF00  # sidetone feature report

STATUS_REQUEST_ID = 0xC9
NOTIFICATION_REQUEST_ID = 0xCA
LIGHTS_REQUEST_ID = 0xC8
STATUS_REPORT_ID = 0x64
FIRMWARE_REPORT_ID = 0x66
REPORT_SIZE = 5
FEATURE_REPORT_SIZE = 64

SIDETONE_MAX = 55
SIDETONE_BASE = 200

# Observed from iCUE's writes: [0xCB, pair count, (register, value)...], 20 bytes.
# Registers are in R, G, B order. Full-scale values apply iCUE's white balance.
LED_REQUEST_ID = 0xCB
LED_REPORT_SIZE = 20
LEFT_LED_REGISTERS = (0x1C, 0x16, 0x17)
RIGHT_LED_REGISTERS = (0x1D, 0x18, 0x19)
LED_GAIN = (0xAF, 0x97, 0x78)

WIRED = 16
WIRELESS_CONNECTED = 177
BATTERY_LOW = 2
BATTERY_CRITICAL = 3
BATTERY_CHARGED = 4
BATTERY_CHARGING = 5

BATTERY_STATES = {
    0: "Indisponible",
    1: "Sur batterie",
    BATTERY_LOW: "Batterie faible",
    BATTERY_CRITICAL: "Batterie critique",
    BATTERY_CHARGED: "Chargé",
    BATTERY_CHARGING: "En charge",
}
CONNECTION_STATES = {
    WIRED: "Filaire",
    38: "Initialisation",
    49: "Connexion perdue",
    51: "Recherche du casque",
    52: "Casque éteint",
    WIRELESS_CONNECTED: "Connecté",
}
NOT_CONNECTED = (38, 49, 51, 52)


@dataclass
class HeadsetStatus:
    dongle_found: bool = False
    connected: bool = False
    battery_percent: Optional[int] = None
    charging: bool = False
    battery_state: str = "Indisponible"
    microphone_raised: Optional[bool] = None
    connection_state: str = "Récepteur introuvable"
    error: Optional[str] = None

    @property
    def discharging(self) -> bool:
        return self.connected and not self.charging and self.battery_percent is not None


def parse_status_report(report: bytes | list[int]) -> HeadsetStatus:
    """Parse [0x64, power, capacity+mic, connection, battery]."""
    data = list(report)
    # HIDAPI may prefix the report with a zero report number.
    if len(data) >= REPORT_SIZE + 1 and data[0] == 0:
        data = data[1:]
    if len(data) < REPORT_SIZE:
        raise ValueError(f"Rapport HID trop court ({len(data)} octets)")
    if data[0] != STATUS_REPORT_ID:
        raise ValueError(f"Identifiant de rapport inattendu : 0x{data[0]:02X}")

    capacity_and_mic, connection_code, battery_code = data[2], data[3], data[4]
    # Undocumented codes were seen with a live headset (161 on battery), so any
    # code that is not a known "no headset" state counts when a battery is reported.
    connected = connection_code in (WIRELESS_CONNECTED, WIRED) or (
        bool(battery_code) and connection_code not in NOT_CONNECTED
    )
    charging = battery_code in (BATTERY_CHARGING, BATTERY_CHARGED)

    # While charging the capacity can read too high (the kernel driver notes
    # about 54), so it only becomes exact once the headset is unplugged.
    percent = None
    if connected and battery_code:
        percent = 100 if battery_code == BATTERY_CHARGED else min(100, capacity_and_mic & 0x7F)

    return HeadsetStatus(
        dongle_found=True,
        connected=connected,
        battery_percent=percent,
        charging=charging,
        battery_state=BATTERY_STATES.get(battery_code, f"État {battery_code}"),
        microphone_raised=bool(capacity_and_mic & 0x80) if connected else None,
        connection_state=CONNECTION_STATES.get(
            connection_code, "Connecté" if connected else f"Code {connection_code}"
        ),
    )


def _led_value(value: int, gain: int) -> int:
    if not 0 <= value <= 255:
        raise ValueError(f"Couleur hors limites : {value}")
    return round(value * gain / 255)


def led_report(left: tuple[int, int, int], right: tuple[int, int, int]) -> list[int]:
    """Build the colour report for both logos, from 0..255 RGB values."""
    pairs: list[int] = []
    for registers, color in ((LEFT_LED_REGISTERS, left), (RIGHT_LED_REGISTERS, right)):
        for register, value, gain in zip(registers, color, LED_GAIN):
            pairs += [register, _led_value(value, gain)]
    report = [LED_REQUEST_ID, len(pairs) // 2] + pairs
    return report + [0] * (LED_REPORT_SIZE - len(report))


def sidetone_report(level: int) -> list[int]:
    """Build the wireless sidetone feature report; level is 0..SIDETONE_MAX."""
    if not 0 <= level <= SIDETONE_MAX:
        raise ValueError(f"Sidetone hors limites : {level}")
    report = [0xFF, 0x0B, 0x00, 0xFF, 0x04, 0x0E, 0xFF, 0x05, 0x01, 0x04, 0x00,
              SIDETONE_BASE + level]
    return report + [0] * (FEATURE_REPORT_SIZE - len(report))


class CorsairVoidElite:
    """Find and drive a VOID ELITE Wireless USB receiver using HIDAPI."""

    def __init__(self) -> None:
        self._handles: dict[int, object] = {}

    @staticmethod
    def available() -> list[dict]:
        if hid is None:
            raise RuntimeError("Module hidapi absent. Lance install.bat.")
        found = []
        for pid in VOID_ELITE_PIDS:
            found.extend(hid.enumerate(CORSAIR_VID, pid))
        return found

    def _open(self, usage_page: int):
        handle = self._handles.get(usage_page)
        if handle is not None:
            return handle
        for info in self.available():
            if info.get("usage_page") == usage_page:
                handle = hid.device()
                handle.open_path(info["path"])
                handle.set_nonblocking(False)
                self._handles[usage_page] = handle
                return handle
        return None

    def close(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()

    def _control(self):
        handle = self._open(USAGE_PAGE_CONTROL)
        if handle is None:
            raise RuntimeError("Récepteur USB Corsair introuvable.")
        return handle

    def _request(self, report_id: int) -> list[int]:
        device = self._control()
        if device.write([STATUS_REQUEST_ID, report_id]) < 0:
            raise RuntimeError("Échec de l'envoi de la requête HID.")
        # Skip unrelated reports until the expected packet arrives.
        for timeout_ms in (900, 250, 250, 250):
            response = device.read(64, timeout_ms=timeout_ms)
            if not response:
                break
            if response[0] == report_id:
                return response
        raise TimeoutError(f"Pas de réponse du récepteur au rapport 0x{report_id:02X}.")

    def read_status(self) -> HeadsetStatus:
        if hid is None:
            return HeadsetStatus(error="Module hidapi absent. Lance install.bat.")
        try:
            return parse_status_report(self._request(STATUS_REPORT_ID))
        except Exception as exc:
            # A stale handle (dongle unplugged) is reopened on the next poll.
            self.close()
            try:
                found = bool(self.available())
            except Exception:
                found = False
            if not found:
                return HeadsetStatus()
            return HeadsetStatus(
                dongle_found=True,
                connection_state="Lecture impossible",
                error=str(exc),
            )

    def read_firmware(self) -> Optional[str]:
        """Return the headset firmware version, or None when it is unavailable."""
        try:
            data = self._request(FIRMWARE_REPORT_ID)
        except Exception:
            self.close()
            return None
        if len(data) < REPORT_SIZE or not (data[3] or data[4]):
            return None
        return f"{data[3]}.{data[4]}"

    def _send(self, action: str, report: list[int]) -> None:
        try:
            written = self._control().write(report)
        except Exception as exc:
            self.close()
            raise RuntimeError(f"{action} : {exc}") from exc
        if written < 0:
            self.close()
            raise RuntimeError(f"{action} : le récepteur a refusé la commande.")

    def set_lighting_mode(self, hardware: bool) -> None:
        """Select who drives the LEDs (behaviour observed on a VOID ELITE Wireless).

        True: the headset's built-in multicolour effect, which ignores colour reports.
        False: software control, through set_leds. LEDs stay off until a colour changes.
        """
        self._send("LED", [LIGHTS_REQUEST_ID, 0x00 if hardware else 0x01, 0x00])

    def set_leds(self, left: tuple[int, int, int], right: tuple[int, int, int]) -> None:
        self._send("Couleur", led_report(left, right))

    def play_alert(self, sound_id: int = 0) -> None:
        if sound_id not in (0, 1):
            raise ValueError(f"Alerte inconnue : {sound_id}")
        self._send("Alerte", [NOTIFICATION_REQUEST_ID, 0x02, sound_id])

    def set_sidetone(self, level: int) -> None:
        report = sidetone_report(level)
        try:
            handle = self._open(USAGE_PAGE_SIDETONE)
            if handle is None:
                raise RuntimeError("interface introuvable")
            written = handle.send_feature_report(report)
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Retour micro : {exc}") from exc
        if written < 0:
            self.close()
            raise RuntimeError("Retour micro : le récepteur a refusé la commande.")


def diagnostic() -> str:
    if hid is None:
        return "Module hidapi absent. Lance install.bat."
    lines = ["Corsair Monitor — diagnostic HID", f"VID Corsair : 0x{CORSAIR_VID:04X}"]
    try:
        devices = CorsairVoidElite.available()
    except Exception as exc:
        return f"Erreur d'énumération HID : {exc}"
    if not devices:
        lines.append("Aucun périphérique VOID ELITE Wireless reconnu.")
        lines.append("Vérifie le dongle USB et le modèle exact du casque.")
    for item in devices:
        lines.append(
            f"PID=0x{item.get('product_id', 0):04X} | "
            f"Interface={item.get('interface_number')} | "
            f"UsagePage=0x{item.get('usage_page', 0):04X} | "
            f"Produit={item.get('product_string')}"
        )
    if devices:
        device = CorsairVoidElite()
        status = device.read_status()
        lines.append(
            f"État : {status.connection_state} | Batterie : {status.battery_state} "
            f"{status.battery_percent if status.battery_percent is not None else ''}"
        )
        lines.append(f"Firmware casque : {device.read_firmware() or 'indisponible'}")
        device.close()
    return "\n".join(lines)
