"""Streamlit exposes complete palettes for its native theme switcher."""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"


def _relative_luminance(color: str) -> float:
    channels = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = (
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )
    return sum(
        channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def test_dashboard_themes() -> None:
    theme = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    assert theme["base"] == "dark"

    dark = theme["dark"]
    assert {
        key: dark[key]
        for key in (
            "primaryColor",
            "backgroundColor",
            "secondaryBackgroundColor",
            "textColor",
            "borderColor",
            "dataframeHeaderBackgroundColor",
        )
    } == {
        "primaryColor": "#4C9BE8",
        "backgroundColor": "#0E1117",
        "secondaryBackgroundColor": "#1C2029",
        "textColor": "#FAFAFA",
        "borderColor": "#2A303C",
        "dataframeHeaderBackgroundColor": "#161B24",
    }
    assert dark["sidebar"] == {
        "backgroundColor": "#12161E",
        "secondaryBackgroundColor": "#1C2029",
    }

    light = theme["light"]
    for key in (
        "primaryColor",
        "backgroundColor",
        "secondaryBackgroundColor",
        "textColor",
        "borderColor",
        "dataframeHeaderBackgroundColor",
    ):
        assert light[key].startswith("#") and len(light[key]) == 7
    assert set(light["sidebar"]) >= {"backgroundColor", "secondaryBackgroundColor"}
    assert all(
        light[key] != dark[key]
        for key in ("backgroundColor", "secondaryBackgroundColor", "dataframeHeaderBackgroundColor")
    )
    assert _relative_luminance(light["backgroundColor"]) > _relative_luminance(light["textColor"])
