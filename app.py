from __future__ import annotations

import ctypes
import sys
import time
import winreg
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMetaObject, QPoint, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QFontDatabase, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSlider, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from backend import Snapshot, Worker
from settings import LOW_BATTERY_PERCENT, Settings, autostart_enabled, set_autostart

APP_NAME = "Corsair Monitor"
APP_ID = "CorsairMonitor.Tray"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"
TRAY_SIZES = (16, 20, 24, 32, 40, 48)

BACKGROUND = "#141518"
BORDER = "#2A2C31"
SURFACE = "#1E2024"
TEXT = "#F2F3F5"
MUTED = "#8B8F98"
GOOD = "#5FCB94"
WARNING = "#E5B05C"
DANGER = "#E5645E"

# Glyph codepoints shared by Segoe Fluent Icons and Segoe MDL2 Assets.
ICON_SPEAKER = ""
ICON_SPEAKER_OFF = ""
ICON_MIC = ""
ICON_MIC_OFF = ""
ICON_SIDETONE = ""
ICON_SETTINGS = ""
ICON_BACK = ""

SWATCHES = ("#FFFFFF", "#E5322D", "#FF8A00", "#FFD400", "#22C55E", "#06B6D4", "#2563EB", "#C026D3")

STYLE = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; color: {TEXT}; }}
QLabel {{ background: transparent; font-size: 12px; }}
QLabel[role="title"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLabel[role="value"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="battery"] {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Segoe UI Symbol";
    font-size: 34px; font-weight: 600;
}}
QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ height: 4px; background: {TEXT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {TEXT};
}}
QSlider::groove:horizontal:disabled {{ background: {SURFACE}; }}
QSlider::sub-page:horizontal:disabled {{ background: {BORDER}; }}
QSlider::handle:horizontal:disabled {{ background: {BORDER}; }}
QPushButton {{
    background: {SURFACE}; border: none; border-radius: 6px; padding: 6px 12px; font-size: 12px;
}}
QPushButton:hover {{ background: {BORDER}; }}
QPushButton[role="segment"] {{ background: transparent; color: {MUTED}; padding: 4px 0; }}
QPushButton[role="segment"]:checked {{ background: {BORDER}; color: {TEXT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; font-size: 12px; }}
QMenu::item:selected {{ background: {BORDER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
"""


def icon_font(size: int = 14) -> QFont:
    families = QFontDatabase.families()
    name = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"
    font = QFont(name)
    font.setPixelSize(size)
    return font


def label(text: str = "", role: str | None = None) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    return widget


class IconButton(QAbstractButton):
    """Borderless glyph button; red when checked (muted)."""

    def __init__(self, glyph: str, checked_glyph: str | None = None, checkable: bool = False):
        super().__init__()
        self._glyph, self._checked_glyph = glyph, checked_glyph or glyph
        self.setCheckable(checkable)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() and self.isEnabled():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SURFACE))
            painter.drawRoundedRect(self.rect(), 6, 6)
        color = DANGER if self.isChecked() else (TEXT if self.isEnabled() else MUTED)
        painter.setPen(QColor(color))
        painter.setFont(icon_font())
        glyph = self._checked_glyph if self.isChecked() else self._glyph
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, glyph)


class Toggle(QAbstractButton):
    """Small iOS-style switch."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(34, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        on = self.isChecked()
        enabled = self.isEnabled()
        painter.setBrush(QColor(TEXT if on and enabled else BORDER))
        painter.drawRoundedRect(self.rect(), 9, 9)
        painter.setBrush(QColor(BACKGROUND if on else (MUTED if enabled else SURFACE)))
        painter.drawEllipse(QRectF(19 if on else 3, 3, 12, 12))


class Swatch(QAbstractButton):
    def __init__(self, color: str):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isChecked():
            painter.setPen(QPen(QColor(TEXT), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(1, 1, 24, 24))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(QRectF(5, 5, 16, 16))


class GradientSlider(QWidget):
    """Horizontal 0..1 slider painted over a colour gradient."""

    moved = Signal(float)

    def __init__(self, stops: list[tuple[float, QColor]]):
        super().__init__()
        self.stops = stops
        self.value = 0.0
        self.setFixedHeight(20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, value: float) -> None:
        self.value = max(0.0, min(1.0, value))
        self.update()

    def set_stops(self, stops: list[tuple[float, QColor]]) -> None:
        self.stops = stops
        self.update()

    def _track(self) -> QRectF:
        return QRectF(7, 7, self.width() - 14, 6)

    def _pick(self, x: float) -> None:
        track = self._track()
        self.set_value((x - track.left()) / track.width())
        self.moved.emit(self.value)

    def mousePressEvent(self, event):
        self._pick(event.position().x())

    def mouseMoveEvent(self, event):
        self._pick(event.position().x())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        gradient = QLinearGradient(track.topLeft(), track.topRight())
        for position, color in self.stops:
            gradient.setColorAt(position, color)
        painter.setOpacity(1.0 if self.isEnabled() else 0.3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, 3, 3)
        center = track.left() + self.value * track.width()
        painter.setBrush(QColor(TEXT))
        painter.drawEllipse(QRectF(center - 7, 3, 14, 14))
        painter.setBrush(QColor(BACKGROUND))
        painter.drawEllipse(QRectF(center - 4, 6, 8, 8))


class BatteryBars(QWidget):
    """Row of segments, one lit per started 10 % of battery."""

    SEGMENTS = 10
    GAP = 4

    def __init__(self):
        super().__init__()
        self.percent = None
        self.color = GOOD
        self.setFixedHeight(10)

    def set_level(self, percent: int | None, color: str) -> None:
        self.percent, self.color = percent, color
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        lit = 0 if self.percent is None else (self.percent + 9) // 10
        width = (self.width() - self.GAP * (self.SEGMENTS - 1)) / self.SEGMENTS
        for index in range(self.SEGMENTS):
            painter.setBrush(QColor(self.color if index < lit else BORDER))
            x = index * (width + self.GAP)
            painter.drawRoundedRect(QRectF(x, 0, width, self.height()), 2.5, 2.5)


class LogoLabel(QWidget):
    def __init__(self, pixmap: QPixmap, size: int):
        super().__init__()
        self.pixmap = pixmap
        self.setFixedSize(size, size)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.pixmap)


class Debounced:
    """Calls `action` with the latest value, at most once per `delay_ms`."""

    def __init__(self, action: Callable, delay_ms: int = 50):
        self._action, self._value = action, None
        self._timer = QTimer(singleShot=True, interval=delay_ms)
        self._timer.timeout.connect(self._fire)

    def __call__(self, value) -> None:
        self._value = value
        if not self._timer.isActive():
            self._timer.start()

    def _fire(self) -> None:
        self._action(self._value)


class Panel(QWidget):
    """The pop-up shown above the notification area."""

    output_volume = Signal(float)
    output_muted = Signal(bool)
    mic_volume = Signal(float)
    mic_muted = Signal(bool)
    sidetone = Signal(int)
    lights = Signal(bool)
    colors = Signal(object)
    alert = Signal(int)
    refresh = Signal()

    def __init__(self, settings: Settings, logo: QPixmap):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.settings = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)
        self.setStyleSheet(STYLE)
        self.hidden_at = 0.0
        self._zone = 0  # 0 both, 1 left, 2 right
        self._save_later = QTimer(self, singleShot=True, interval=500)
        self._save_later.timeout.connect(self.settings.save)
        self._send_volume = Debounced(self.output_volume.emit)
        self._send_mic = Debounced(self.mic_volume.emit)
        self._send_sidetone = Debounced(self.sidetone.emit, 80)
        self._send_colors = Debounced(self.colors.emit)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("QStackedWidget { background: transparent; }")
        self.pages.addWidget(self._build_main(logo))
        self.pages.addWidget(self._build_settings())
        root.addWidget(self.pages)
        self._load_color_controls()

    # ------------------------------------------------------------------ pages
    def _build_main(self, logo: QPixmap) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(LogoLabel(logo, 40))
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addStretch()
        names.addWidget(label("VOID ELITE Wireless", "title"))
        self.status_label = label("Recherche…", "muted")
        names.addWidget(self.status_label)
        names.addStretch()
        header.addLayout(names)
        header.addStretch()
        layout.addLayout(header)

        layout.addSpacing(14)
        battery = QHBoxLayout()
        self.battery_value = label("—", "battery")
        battery.addWidget(self.battery_value)
        battery.addStretch()
        self.battery_state = label("", "muted")
        battery.addWidget(self.battery_state, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(battery)
        layout.addSpacing(6)
        self.battery_bars = BatteryBars()
        layout.addWidget(self.battery_bars)

        layout.addSpacing(18)
        layout.addWidget(label("AUDIO", "section"))
        layout.addSpacing(6)

        self.output_mute = IconButton(ICON_SPEAKER, ICON_SPEAKER_OFF, checkable=True)
        self.output_mute.setToolTip("Couper le son")
        self.output_mute.clicked.connect(lambda checked: self.output_muted.emit(checked))
        self.output_slider, self.output_text = self._slider_row(
            layout, self.output_mute, lambda v: self._send_volume(v / 100)
        )
        self.mic_mute = IconButton(ICON_MIC, ICON_MIC_OFF, checkable=True)
        self.mic_mute.setToolTip("Couper le micro")
        self.mic_mute.clicked.connect(lambda checked: self.mic_muted.emit(checked))
        self.mic_slider, self.mic_text = self._slider_row(
            layout, self.mic_mute, lambda v: self._send_mic(v / 100)
        )
        sidetone_icon = IconButton(ICON_SIDETONE)
        sidetone_icon.setToolTip("Retour micro : t'entendre dans le casque")
        sidetone_icon.setEnabled(False)
        self.sidetone_slider, self.sidetone_text = self._slider_row(
            layout, sidetone_icon, self._on_sidetone
        )
        if self.settings.sidetone_percent is None:
            self.sidetone_text.setText("—")
        else:
            # The worker restores this level itself: do not send it again here.
            self.sidetone_slider.blockSignals(True)
            self.sidetone_slider.setValue(self.settings.sidetone_percent)
            self.sidetone_slider.blockSignals(False)
            self.sidetone_text.setText(f"{self.settings.sidetone_percent} %")

        layout.addSpacing(14)
        title = QHBoxLayout()
        title.addWidget(label("ÉCLAIRAGE", "section"))
        title.addStretch()
        title.addWidget(label("Couleur perso", "muted"))
        title.addSpacing(6)
        self.custom_toggle = Toggle()
        self.custom_toggle.setChecked(self.settings.custom_color)
        self.custom_toggle.clicked.connect(self._on_custom_toggle)
        title.addWidget(self.custom_toggle)
        layout.addLayout(title)
        layout.addSpacing(8)

        self.color_box = QWidget()
        box = QVBoxLayout(self.color_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        zones = QHBoxLayout()
        zones.setSpacing(2)
        self.zone_group = QButtonGroup(self)
        for index, text in enumerate(("Les deux", "Gauche", "Droite")):
            button = QPushButton(text)
            button.setProperty("role", "segment")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.zone_group.addButton(button, index)
            zones.addWidget(button)
        self.zone_group.idClicked.connect(self._on_zone)
        box.addLayout(zones)

        swatches = QHBoxLayout()
        swatches.setSpacing(0)
        self.swatch_group = QButtonGroup(self)
        for color in SWATCHES:
            swatch = Swatch(color)
            self.swatch_group.addButton(swatch)
            swatch.clicked.connect(lambda _=False, c=color: self._pick_color(QColor(c)))
            swatches.addWidget(swatch)
            swatches.addStretch()
        swatches.takeAt(swatches.count() - 1)
        box.addLayout(swatches)

        hues = [(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1, 1)) for i in range(7)]
        self.hue_slider = GradientSlider(hues)
        self.hue_slider.setToolTip("Teinte")
        self.hue_slider.moved.connect(self._on_hue)
        box.addWidget(self.hue_slider)
        self.brightness_slider = GradientSlider([(0, QColor("#000")), (1, QColor("#fff"))])
        self.brightness_slider.setToolTip("Intensité")
        self.brightness_slider.moved.connect(self._on_brightness)
        box.addWidget(self.brightness_slider)
        layout.addWidget(self.color_box)

        layout.addSpacing(12)
        self.error_label = label("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {WARNING}; font-size: 11px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        footer = QHBoxLayout()
        self.mic_position = label("", "muted")
        footer.addWidget(self.mic_position)
        footer.addStretch()
        gear = IconButton(ICON_SETTINGS)
        gear.setToolTip("Paramètres")
        gear.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        footer.addWidget(gear)
        layout.addLayout(footer)
        return page

    def _build_settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        back = IconButton(ICON_BACK)
        back.setToolTip("Retour")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        header.addWidget(back)
        header.addSpacing(4)
        header.addWidget(label("Paramètres", "title"))
        header.addStretch()
        layout.addLayout(header)
        layout.addSpacing(4)

        self.autostart_toggle = Toggle()
        self.autostart_toggle.setChecked(autostart_enabled())
        self.autostart_toggle.clicked.connect(self._on_autostart)
        self._toggle_row(layout, "Démarrer avec Windows", self.autostart_toggle)

        self.lights_toggle = Toggle()
        self.lights_toggle.setChecked(self.settings.lights_on is not False)
        self.lights_toggle.clicked.connect(self._on_lights)
        self._toggle_row(layout, "LED du casque", self.lights_toggle)

        self.alert_toggle = Toggle()
        self.alert_toggle.setChecked(self.settings.low_battery_alert)
        self.alert_toggle.clicked.connect(self._on_alert_toggle)
        self._toggle_row(layout, f"Alerte batterie sous {LOW_BATTERY_PERCENT} %", self.alert_toggle)

        test = QHBoxLayout()
        test.addWidget(label("Bip du casque"))
        test.addStretch()
        beep = QPushButton("Tester")
        beep.setCursor(Qt.CursorShape.PointingHandCursor)
        beep.clicked.connect(lambda: self.alert.emit(0))
        test.addWidget(beep)
        layout.addLayout(test)

        layout.addSpacing(8)
        layout.addWidget(label("INFORMATIONS", "section"))
        self.info = {}
        row = QHBoxLayout()
        row.addWidget(label("Firmware du casque"))
        row.addStretch()
        value = label("—", "value")
        row.addWidget(value)
        layout.addLayout(row)
        self.info["firmware"] = value
        layout.addStretch()
        return page

    def _slider_row(self, parent, icon: QWidget, on_change: Callable[[int], None]):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(icon)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        row.addWidget(slider)
        text = label("0 %", "value")
        text.setFixedWidth(36)
        text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(text)
        slider.valueChanged.connect(lambda value: text.setText(f"{value} %"))
        slider.valueChanged.connect(on_change)
        parent.addLayout(row)
        return slider, text

    @staticmethod
    def _toggle_row(parent, text: str, toggle: Toggle) -> None:
        row = QHBoxLayout()
        row.addWidget(label(text))
        row.addStretch()
        row.addWidget(toggle)
        parent.addLayout(row)

    # ---------------------------------------------------------------- handlers
    def _store(self) -> None:
        self._save_later.start()

    def _on_sidetone(self, value: int) -> None:
        self.settings.sidetone_percent = value
        self._store()
        self._send_sidetone(value)

    def _on_lights(self, enabled: bool) -> None:
        self.settings.lights_on = enabled
        self._store()
        self.lights.emit(enabled)

    def _on_alert_toggle(self, enabled: bool) -> None:
        self.settings.low_battery_alert = enabled
        self._store()

    def _on_autostart(self, enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except OSError as exc:
            self.show_error(f"Démarrage automatique : {exc}")
        self.autostart_toggle.setChecked(autostart_enabled())

    def _on_custom_toggle(self, enabled: bool) -> None:
        self.settings.custom_color = enabled
        self._store()
        self._update_color_enabled()
        self._send_colors(list(self.settings.colors) if enabled else None)

    def _on_zone(self, zone: int) -> None:
        self._zone = zone
        self._load_color_controls()

    def _zone_color(self) -> QColor:
        return QColor(self.settings.colors[1 if self._zone == 2 else 0])

    def _load_color_controls(self) -> None:
        color = self._zone_color()
        self.hue_slider.set_value(max(color.hsvHueF(), 0.0))
        self.brightness_slider.set_value(color.valueF())
        self._sync_swatches(color)
        self._update_brightness_gradient(color)
        self._update_color_enabled()

    def _update_color_enabled(self) -> None:
        self.color_box.setEnabled(self.custom_toggle.isChecked())
        self.color_box.setVisible(self.custom_toggle.isChecked())
        self.adjustSize()
        self._reposition()

    def _sync_swatches(self, color: QColor) -> None:
        self.swatch_group.setExclusive(False)
        for button in self.swatch_group.buttons():
            button.setChecked(QColor(button.color).rgb() == color.rgb())
        self.swatch_group.setExclusive(True)

    def _update_brightness_gradient(self, color: QColor) -> None:
        bright = QColor.fromHsvF(max(color.hsvHueF(), 0.0), color.hsvSaturationF(), 1.0)
        self.brightness_slider.set_stops([(0, QColor("#000")), (1, bright)])

    def _pick_color(self, color: QColor) -> None:
        self.brightness_slider.set_value(color.valueF())
        self.hue_slider.set_value(max(color.hsvHueF(), 0.0))
        self._apply_color(color)

    def _on_hue(self, hue: float) -> None:
        value = max(self.brightness_slider.value, 0.05)
        self._apply_color(QColor.fromHsvF(min(hue, 0.999), 1.0, value))

    def _on_brightness(self, value: float) -> None:
        current = self._zone_color()
        hue = max(current.hsvHueF(), 0.0)
        self._apply_color(QColor.fromHsvF(hue, current.hsvSaturationF(), value))

    def _apply_color(self, color: QColor) -> None:
        name = color.name().upper()
        if self._zone in (0, 1):
            self.settings.colors[0] = name
        if self._zone in (0, 2):
            self.settings.colors[1] = name
        self._store()
        self._sync_swatches(color)
        self._update_brightness_gradient(color)
        self._send_colors(list(self.settings.colors))

    # ------------------------------------------------------------------ update
    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def update_from(self, state: Snapshot) -> None:
        headset, audio = state.headset, state.audio

        color = GOOD if headset.connected else WARNING
        self.status_label.setText(headset.connection_state)
        self.status_label.setStyleSheet(f"color: {color};")
        percent = headset.battery_percent
        self.battery_value.setText("—" if percent is None else f"{percent} %")
        # U+FE0E asks for the monochrome glyph instead of the colour emoji.
        charging_mark = "⚡︎ " if headset.charging else ""
        self.battery_state.setText(
            charging_mark + headset.battery_state if headset.connected else ""
        )
        low = headset.discharging and percent <= LOW_BATTERY_PERCENT
        self.battery_value.setStyleSheet(f"color: {DANGER};" if low else "")
        self.battery_bars.set_level(percent, DANGER if low else GOOD)

        self._set_audio(self.output_slider, self.output_mute, audio.output_found,
                        audio.output_volume, audio.output_muted, self.output_text)
        self._set_audio(self.mic_slider, self.mic_mute, audio.mic_found,
                        audio.mic_volume, audio.mic_muted, self.mic_text)
        self.sidetone_slider.setEnabled(headset.connected)

        if headset.microphone_raised is None:
            self.mic_position.setText("")
        else:
            self.mic_position.setText("Bras du micro relevé" if headset.microphone_raised
                                      else "Bras du micro abaissé")

        self.info["firmware"].setText(state.firmware or "—")

    @staticmethod
    def _set_audio(slider, mute, found, volume, muted, text) -> None:
        slider.setEnabled(found)
        mute.setEnabled(found)
        mute.setChecked(found and muted)
        if not found:
            text.setText("—")
            return
        if not slider.isSliderDown():
            slider.blockSignals(True)
            slider.setValue(round(volume * 100))
            slider.blockSignals(False)
            text.setText(f"{slider.value()} %")

    # ---------------------------------------------------------------- geometry
    def _reposition(self) -> None:
        if self.isVisible():
            self.place(self._anchor)

    _anchor = QPoint()

    def place(self, anchor: QPoint) -> None:
        self._anchor = anchor
        self.adjustSize()
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        margin = 10
        x = min(max(anchor.x() - self.width() // 2, area.left() + margin),
                area.right() - self.width() - margin)
        if anchor.y() > area.center().y():
            y = area.bottom() - self.height() - margin
        else:
            y = area.top() + margin
        self.move(x, y)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.fillPath(path, QColor(BACKGROUND))
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.drawPath(path)

    def changeEvent(self, event):
        # Behave like a system flyout: clicking elsewhere closes the panel.
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow():
            if self.isVisible():
                self.hidden_at = time.monotonic()
                self.hide()
                self.pages.setCurrentIndex(0)
        super().changeEvent(event)


def uses_light_taskbar() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def tray_pixmap(logo: QImage, size: int, color: QColor, dot: QColor | None, opacity: float) -> QPixmap:
    """Scale the logo for a tray size; small sizes get bolder edges to stay readable."""
    scaled = logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    if size <= 32:
        scaled = scaled.convertToFormat(QImage.Format.Format_ARGB32)
        boost = 0.55 if size <= 20 else 0.75
        for y in range(scaled.height()):
            for x in range(scaled.width()):
                pixel = scaled.pixelColor(x, y)
                pixel.setAlphaF(pixel.alphaF() ** boost)
                scaled.setPixelColor(x, y, pixel)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(opacity)
    painter.drawImage(0, 0, scaled)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.setOpacity(1.0)
    painter.fillRect(pixmap.rect(), color)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    if dot is not None:
        radius = max(3.0, size * 0.2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(size - 2 * radius, size - 2 * radius, 2 * radius, 2 * radius))
    painter.end()
    return pixmap


class TrayApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        self.logo = QImage(str(LOGO_PATH))
        self._icon_key = None
        self._low_notified = False

        if self.settings.first_run:
            self.settings.first_run = False
            try:
                set_autostart(True)
            except OSError:
                pass
            self.settings.save()

        logo_pixmap = QPixmap.fromImage(self.logo)
        self.panel = Panel(self.settings, logo_pixmap)
        self.app.setWindowIcon(QIcon(logo_pixmap))

        self.tray = QSystemTrayIcon(app)
        self.tray.setToolTip(APP_NAME)
        self._set_tray_icon(None, False)
        # Keep a reference: Qt does not take ownership of the context menu.
        self.menu = self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.thread = QThread()
        colors = list(self.settings.colors) if self.settings.custom_color else None
        self.worker = Worker(self.settings.sidetone_percent, self.settings.lights_on, colors)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.snapshot.connect(self.on_snapshot)
        self.worker.error.connect(self.panel.show_error)
        self.panel.output_volume.connect(self.worker.set_output_volume)
        self.panel.output_muted.connect(self.worker.set_output_muted)
        self.panel.mic_volume.connect(self.worker.set_mic_volume)
        self.panel.mic_muted.connect(self.worker.set_mic_muted)
        self.panel.sidetone.connect(self.worker.set_sidetone)
        self.panel.lights.connect(self.worker.set_lights)
        self.panel.colors.connect(self.worker.set_colors)
        self.panel.alert.connect(self.worker.play_alert)
        self.panel.refresh.connect(self.worker.refresh)
        self.thread.start()

        self.server = QLocalServer(app)
        QLocalServer.removeServer(APP_ID)
        self.server.newConnection.connect(self._on_second_instance)
        self.server.listen(APP_ID)

    def _on_second_instance(self) -> None:
        self.server.nextPendingConnection().disconnectFromServer()
        self.show_panel()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(STYLE)
        open_action = QAction("Ouvrir", menu)
        open_action.triggered.connect(self.show_panel)
        menu.addAction(open_action)
        self.mute_action = QAction("Couper le son", menu, checkable=True)
        self.mute_action.triggered.connect(lambda checked: self.panel.output_muted.emit(checked))
        menu.addAction(self.mute_action)
        self.mic_action = QAction("Couper le micro", menu, checkable=True)
        self.mic_action.triggered.connect(lambda checked: self.panel.mic_muted.emit(checked))
        menu.addAction(self.mic_action)
        menu.addSeparator()
        quit_action = QAction("Quitter", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        return menu

    def _set_tray_icon(self, dot: QColor | None, connected: bool) -> None:
        light = uses_light_taskbar()
        key = (light, dot.name() if dot else None, connected)
        if key == self._icon_key or self.logo.isNull():
            return
        self._icon_key = key
        color = QColor("#000000" if light else "#FFFFFF")
        opacity = 1.0 if connected else 0.45
        icon = QIcon()
        for size in TRAY_SIZES:
            icon.addPixmap(tray_pixmap(self.logo, size, color, dot, opacity))
        self.tray.setIcon(icon)

    def on_snapshot(self, state: Snapshot) -> None:
        headset = state.headset
        self.panel.update_from(state)
        self.mute_action.setEnabled(state.audio.output_found)
        self.mute_action.setChecked(state.audio.output_muted)
        self.mic_action.setEnabled(state.audio.mic_found)
        self.mic_action.setChecked(state.audio.mic_muted)

        low = headset.discharging and headset.battery_percent <= LOW_BATTERY_PERCENT
        self._set_tray_icon(QColor(DANGER) if low else None, headset.connected)

        if headset.battery_percent is not None:
            battery = f"{headset.battery_percent} %"
            if headset.charging:
                battery += ", en charge"
        else:
            battery = headset.connection_state.lower()
        self.tray.setToolTip(f"VOID ELITE Wireless — {battery}")

        if low and not self._low_notified and self.settings.low_battery_alert:
            self.tray.showMessage(
                APP_NAME, f"Batterie du casque : {headset.battery_percent} %",
                QSystemTrayIcon.MessageIcon.Warning, 5000,
            )
            self.panel.alert.emit(0)
        # Re-arm only after a real recovery, so the alert does not repeat.
        self._low_notified = low or (self._low_notified and not headset.charging)

    def on_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return
        # A click on the icon first closes the flyout through focus loss:
        # without this check that same click would immediately reopen it.
        if time.monotonic() - self.panel.hidden_at < 0.25:
            return
        self.show_panel()

    def show_panel(self) -> None:
        geometry = self.tray.geometry()
        anchor = geometry.center() if geometry.isValid() and not geometry.isEmpty() else QCursor.pos()
        self.panel.refresh.emit()
        self.panel.show()
        self.panel.place(anchor)
        self.panel.raise_()
        self.panel.activateWindow()

    def quit(self) -> None:
        self.settings.save()
        QMetaObject.invokeMethod(self.worker, "stop", Qt.ConnectionType.QueuedConnection)
        self.thread.wait(3000)
        self.tray.hide()
        self.app.quit()


def already_running() -> bool:
    """Ask a running instance to open its panel; True if there is one."""
    socket = QLocalSocket()
    socket.connectToServer(APP_ID)
    return socket.waitForConnected(300)


def main() -> int:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    if already_running():
        return 0
    tray = TrayApp(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
