"""User settings (JSON in %APPDATA%) and Windows start-up registration."""
from __future__ import annotations

import json
import os
import sys
import winreg
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "CorsairMonitor"
CONFIG_FILE = CONFIG_DIR / "settings.json"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "CorsairMonitor"
LOW_BATTERY_PERCENT = 15


@dataclass
class Settings:
    first_run: bool = True
    # None means "never changed here": the headset keeps its own setting.
    sidetone_percent: Optional[int] = None
    lights_on: Optional[bool] = None
    custom_color: bool = False
    colors: list[str] = field(default_factory=lambda: ["#FFFFFF", "#FFFFFF"])
    low_battery_alert: bool = True

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(CONFIG_FILE)


def _start_command() -> str:
    # pythonw has no console window: the app stays in the notification area only.
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.exists():
        executable = windowless
    script = Path(__file__).resolve().parent / "app.py"
    return f'"{executable}" "{script}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _start_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
