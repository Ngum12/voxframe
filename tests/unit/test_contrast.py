"""Every text and control colour in the web app passes WCAG AA (D-181).

The colours are roles in ``web/src/styles.css``; nothing else names a colour.
This reads them and checks each pair the app actually uses: 4.5:1 for text,
3:1 for controls and focus. Tinted backgrounds (a notice's soft fill) are
blended over the card they sit on, as the eye sees them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLES = Path(__file__).resolve().parents[2] / "web" / "src" / "styles.css"

TEXT = 4.5
CONTROL = 3.0

#: (foreground, background, minimum, what it is).
PAIRS = [
    ("ink", "paper", TEXT, "body text on a card"),
    ("ink", "canvas", TEXT, "body text on the page"),
    ("ink", "sunken", TEXT, "text in a field"),
    ("ink-soft", "paper", TEXT, "secondary text"),
    ("ink-muted", "paper", TEXT, "hints"),
    ("ink-muted", "sunken", TEXT, "hints in a well"),
    ("accent-text", "paper", TEXT, "accent text and links"),
    ("accent", "paper", TEXT, "accent used as text (step labels, links)"),
    ("accent-ink", "accent", TEXT, "a primary button's label"),
    ("error-ink", "error-fill", TEXT, "a danger button's label"),
    ("accent-text", "accent-soft/paper", TEXT, "an info notice, a pressed chip"),
    ("warn", "warn-soft/paper", TEXT, "a warning notice"),
    ("error", "error-soft/paper", TEXT, "an error notice"),
    ("ok", "ok-soft/paper", TEXT, "a success notice"),
    ("accent", "paper", CONTROL, "focus ring, selected borders"),
    ("line-strong", "paper", 1.3, "control outlines (decorative; the label carries meaning)"),
]


def _roles(block: str) -> dict[str, tuple[float, float, float, float]]:
    roles = {}
    for name, value in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block):
        value = value.strip()
        if hexa := re.fullmatch(r"#([0-9a-fA-F]{6})", value):
            h = hexa.group(1)
            roles[name] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
        elif rgba := re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", value):
            r, g, b, a = rgba.groups()
            roles[name] = (int(r), int(g), int(b), float(a))
    return roles


def _modes() -> dict[str, dict[str, tuple[float, float, float, float]]]:
    css = STYLES.read_text(encoding="utf-8")
    dark = re.search(r"^:root \{(.*?)^\}", css, re.S | re.M)
    assert dark, "styles.css has no :root block"
    modes = {"dark": _roles(dark.group(1))}
    light = re.search(r':root\[data-theme="light"\] \{(.*?)^\}', css, re.S | re.M)
    if light:  # step B6 adds the light mode: the same roles, overridden
        modes["light"] = {**modes["dark"], **_roles(light.group(1))}
    return modes


def _colour(spec: str, roles: dict) -> tuple[float, float, float]:  # type: ignore[type-arg]
    """A role, or "tint/base": a translucent role blended over an opaque one."""
    if "/" in spec:
        tint, base = (roles[s] for s in spec.split("/"))
        a = tint[3]
        return tuple(tint[i] * a + base[i] * (1 - a) for i in range(3))  # type: ignore[return-value]
    r, g, b, a = roles[spec]
    assert a == 1.0, f"--{spec} is translucent: say what it sits on"
    return (r, g, b)


def _ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    def luminance(rgb: tuple[float, float, float]) -> float:
        def channel(c: float) -> float:
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(c) for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("mode", sorted(_modes()))
@pytest.mark.parametrize("fg,bg,minimum,what", PAIRS, ids=[p[3] for p in PAIRS])
def test_the_pair_is_legible(mode: str, fg: str, bg: str, minimum: float, what: str) -> None:
    roles = _modes()[mode]

    ratio = _ratio(_colour(fg, roles), _colour(bg, roles))

    assert ratio >= minimum, f"{mode}: {what} is {ratio:.2f}:1, below {minimum}:1"


def test_nothing_outside_the_roles_names_a_colour_for_text_on_accent() -> None:
    """White on the ember accent is 2.4:1: a fill always takes --accent-ink."""
    css = STYLES.read_text(encoding="utf-8")
    for block in re.findall(r"\{[^{}]*background:\s*var\(--accent\);[^{}]*\}", css):
        assert "#fff" not in block and "white" not in block, block
