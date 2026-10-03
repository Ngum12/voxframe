"""Make the pop-up stickers from Microsoft's Fluent Emoji (MIT) (D-198).

Run once, by a maintainer, when the set changes; the PNGs and the manifest
are committed, so nothing here runs when Voxframe is installed or used.

    npm pack fluentui-emoji@1.3.0 && tar xzf fluentui-emoji-1.3.0.tgz
    python scripts/make_stickers.py package/icons/flat

Each flat SVG is drawn by Chromium (Playwright) at 512 x 512 on a transparent
background: the same artwork the studio shows, at a size that stays sharp at
half the width of a 1080-wide video.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "voxframe" / "assets" / "stickers"
SIZE = 512

#: name in Fluent Emoji -> (label for people, words that suggest it).
STICKERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "fire": ("Fire", ("fire", "hot", "burn", "burning", "lit", "amazing")),
    "hundred-points": ("100", ("hundred", "perfect", "100", "totally", "absolutely")),
    "check-mark-button": ("Tick", ("correct",)),
    "cross-mark": ("Cross", ("wrong", "mistake", "fail")),
    "warning": ("Warning", ("warning", "careful", "danger", "dangerous", "risk", "beware")),
    "light-bulb": ("Idea", ("idea", "tip", "realise", "realize")),
    "red-heart": ("Heart", ("love", "loved")),
    "star": ("Star", ()),
    "glowing-star": ("Glowing star", ("brilliant",)),
    "sparkles": ("Sparkles", ("magic", "beautiful")),
    "party-popper": ("Party", ("celebrate", "party", "congratulations")),
    "clapping-hands": ("Applause", ("bravo", "applause")),
    "thumbs-up": ("Thumbs up", ()),
    "thumbs-down": ("Thumbs down", ()),
    "raising-hands": ("Hooray", ("hooray", "yay")),
    "flexed-biceps": ("Strong", ()),
    "eyes": ("Eyes", ()),
    "thinking-face": ("Thinking", ()),
    "face-with-open-mouth": ("Surprised", ("wow", "surprise", "surprising", "shocked")),
    "exploding-head": ("Mind blown", ("crazy", "insane", "incredible", "unbelievable")),
    "loudly-crying-face": ("Crying", ("tragic", "loss")),
    "face-with-tears-of-joy": ("Laughing", ("hilarious",)),
    "smiling-face-with-sunglasses": ("Cool", ()),
    "rocket": ("Rocket", ("launch", "growth", "rocket")),
    "money-bag": ("Money", ("money", "cash")),
    "money-with-wings": ("Spending", ("expensive",)),
    "chart-increasing": ("Up", ("increase", "rise", "rising", "higher")),
    "chart-decreasing": ("Down", ("decrease", "fall", "falling", "lower")),
    "megaphone": ("Announce", ("announce", "news")),
    "bell": ("Bell", ("subscribe", "notification", "reminder")),
    "round-pushpin": ("Place", ("location",)),
    "globe-showing-europe-africa": ("Globe", ("world", "africa", "cameroon", "global", "country")),
    "water-wave": ("Wave", ("water", "flood", "floods", "flooding", "ocean", "river")),
    "cloud-with-rain": ("Rain", ("rain", "storm", "rainy")),
    "house": ("Home", ()),
    "police-car-light": ("Alarm", ("emergency", "alert", "urgent", "breaking")),
    "stop-sign": ("Stop", ("stop", "avoid", "quit")),
    "backhand-index-pointing-down": ("Point down", ()),
    "backhand-index-pointing-right": ("Point right", ()),
    "red-question-mark": ("Question", ()),
    "red-exclamation-mark": ("Exclamation", ()),
    "alarm-clock": ("Time", ("deadline",)),
    "books": ("Books", ("school",)),
    "graduation-cap": ("Graduate", ("student", "university", "graduate", "degree")),
    "gem-stone": ("Gem", ("valuable", "precious", "secret")),
    "trophy": ("Trophy", ("winner", "champion", "award")),
    "crown": ("Crown", ("king", "queen")),
    "folded-hands": ("Thank you", ("thanks", "thank", "pray", "grateful")),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("flat", type=Path, help="fluentui-emoji's icons/flat folder")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    ASSETS.mkdir(parents=True, exist_ok=True)
    manifest = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
        for name, (label, words) in STICKERS.items():
            source = next(
                (args.flat / f"{name}{suffix}.svg" for suffix in ("", "-default")
                 if (args.flat / f"{name}{suffix}.svg").is_file()),
                None,
            )
            if source is None:
                raise SystemExit(f"{name}: not in {args.flat}")
            svg = source.read_text(encoding="utf-8")
            page.set_content(
                "<html><body style='margin:0;background:transparent'>"
                f"<div style='width:{SIZE}px;height:{SIZE}px'>{svg}</div>"
                "<style>svg{width:100%;height:100%;display:block}</style></body></html>"
            )
            page.locator("div").screenshot(path=str(ASSETS / f"{name}.png"), omit_background=True)
            manifest.append({"name": name, "label": label, "words": list(words)})
        browser.close()
    (ASSETS / "stickers.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(manifest)} stickers in {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
