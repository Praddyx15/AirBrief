"""Contrast of the web app's colour tokens (frontend/app/globals.css), both themes, and the
generated token blocks in step with frontend/design/suite-tokens.json."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CSS = (FRONTEND / "app" / "globals.css").read_text(encoding="utf-8")
SURFACES = ["bg", "surface", "elevated"]
BODY_TEXT = ["text", "text-muted", "accent-text"]
# status colours always come with an icon and a word, so body-text contrast is enough
STATUS_TEXT = ["warn", "ok"]
ALERT_TEXT = ["danger"]


def _theme(selector: str) -> dict[str, str]:
    start = CSS.index(selector)
    body = CSS[start : CSS.index("}", start)]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", body))


THEMES = {"dark": _theme(":root {"), "light": _theme(".light {")}


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("token", BODY_TEXT + STATUS_TEXT)
def test_text_tokens_reach_4_5_to_1(theme: str, surface: str, token: str) -> None:
    t = THEMES[theme]
    ratio = contrast(t[token], t[surface])
    assert ratio >= 4.5, f"{theme} --{token} on --{surface}: {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("token", ALERT_TEXT)
def test_alert_text_reaches_7_to_1(theme: str, surface: str, token: str) -> None:
    t = THEMES[theme]
    ratio = contrast(t[token], t[surface])
    assert ratio >= 7.0, f"{theme} --{token} on --{surface}: {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_rgb_channels_match_the_hex(theme: str) -> None:
    start = CSS.index(":root {" if theme == "dark" else ".light {")
    body = CSS[start : CSS.index("}", start)]
    channels = dict(re.findall(r"--([\w-]+)-rgb:\s*([\d ]+);", body))
    for name, hex_value in THEMES[theme].items():
        if name in channels:
            expected = " ".join(str(int(hex_value[i : i + 2], 16)) for i in (1, 3, 5))
            assert channels[name] == expected, name


def test_suite_values_are_used() -> None:
    suite = json.loads((FRONTEND / "design" / "suite-tokens.json").read_text(encoding="utf-8"))["themes"]
    for theme, css_name, token in [("dark", "bg", "bg0"), ("dark", "danger", "warnRedText"), ("light", "danger", "warnRedText"),
                                   ("light", "text", "fg0"), ("dark", "warn", "cautionAmber")]:
        assert THEMES[theme][css_name] == suite[theme][token].lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_generated_blocks_are_up_to_date() -> None:
    result = subprocess.run(
        ["node", "scripts/gen-tokens.mjs", "--check"], cwd=FRONTEND, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
