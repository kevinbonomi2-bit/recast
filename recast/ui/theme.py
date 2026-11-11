"""Colours, fonts and op icons for the wheel and the progress cards (see .oil-ui/wheel-directions/DESIGN-NOTES.md)."""

from __future__ import annotations

import winreg
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont


@dataclass(frozen=True)
class Palette:
    dark: bool
    track: QColor
    tile: QColor
    text: QColor
    meta: QColor
    keyline: QColor
    hub: QColor
    shadow: QColor
    accents: dict[str, QColor]
    error: QColor


LIGHT = Palette(
    dark=False,
    track=QColor("#E9ECF1"), tile=QColor("#FFFFFF"), text=QColor("#14161B"), meta=QColor("#6B7280"),
    keyline=QColor(20, 22, 27, 26), hub=QColor("#FFFFFF"), shadow=QColor(16, 24, 40, 26),
    accents={"convert": QColor("#2F6BFF"), "tools": QColor("#0F9F6E"), "actions": QColor("#7A5AF8")},
    error=QColor("#E5484D"),
)
DARK = Palette(
    dark=True,
    track=QColor("#17191E"), tile=QColor("#2A2D35"), text=QColor("#F3F4F6"), meta=QColor("#9CA3AF"),
    keyline=QColor(255, 255, 255, 23), hub=QColor("#24272E"), shadow=QColor(0, 0, 0, 115),
    accents={"convert": QColor("#4C82FF"), "tools": QColor("#22B981"), "actions": QColor("#9479FF")},
    error=QColor("#F2555A"),
)


def windows_uses_light_theme() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def palette(setting: str = "system") -> Palette:
    if setting == "light":
        return LIGHT
    if setting == "dark":
        return DARK
    return LIGHT if windows_uses_light_theme() else DARK


def font(px: float, weight: QFont.Weight = QFont.Weight.Normal, display: bool = False) -> QFont:
    f = QFont("Segoe UI Variable Display" if display else "Segoe UI Variable Text")
    f.setPixelSize(round(px))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


def with_alpha(c: QColor, alpha: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return out


# Tools and Actions get an icon above the label; format conversions are text only.
OP_ICONS = {
    "image.compress": "arrow_minimize", "image.strip": "tag_dismiss", "image.rotate_right": "arrow_rotate_clockwise",
    "image.rotate_left": "arrow_rotate_counterclockwise", "image.flip": "flip_horizontal", "image.half": "resize",
    "image.combine_pdf": "document_pdf",
    "audio.compress": "arrow_minimize", "audio.normalize": "speaker_2",
    "video.compress": "arrow_minimize", "video.mute": "speaker_mute", "video.extract_audio": "music_note_2",
    "video.make_gif": "gif", "video.rotate": "arrow_rotate_clockwise",
    "pdf.compress": "arrow_minimize", "pdf.merge": "document_pdf", "pdf.split": "split_vertical",
    "pdf.rotate": "arrow_rotate_clockwise", "pdf.strip": "tag_dismiss", "pdf.extract_images": "image_multiple",
    "archive.extract": "archive",
    "image.crop": "crop", "video.trim": "cut", "audio.trim": "cut",
    "image.compress_to": "target_arrow", "video.compress_to": "target_arrow", "audio.compress_to": "target_arrow",
    "pdf.compress_to": "target_arrow", "video.discord": "send",
    "action.copy": "copy", "action.copy_path": "clipboard_text_ltr", "action.move": "folder_arrow_right",
    "action.terminal": "window_console", "action.reveal": "folder_open", "any.zip": "folder_zip",
}
