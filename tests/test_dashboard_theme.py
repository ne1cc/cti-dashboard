"""Streamlit exposes complete palettes for its native theme switcher."""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"


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
    assert int(light["backgroundColor"][1:], 16) > int(light["textColor"][1:], 16)
