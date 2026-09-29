#!/usr/bin/env python3
"""Download a real-photograph evaluation pool with full provenance.

Run::

    python scripts/fetch_eval_images.py              # ~350 photos, 6 per concept
    python scripts/fetch_eval_images.py --per 8      # larger pool
    python scripts/fetch_eval_images.py --dry-run    # check availability only

Why this exists
---------------
The generated pool (``tests/eval/generate.py``) is flat vector art with one
characteristic hue per concept. CLIP is trained on photographs, so a model
choice justified on that pool is not justified for real use (D-043). This
downloads actual photographs for the same 50 concepts, including the confusable
pairs, so the encoder comparison can be re-run on representative content.

Sources, all public-domain or CC0
---------------------------------
- **Openverse** — aggregates CC0 images from Flickr, StockSnap, Wikimedia and
  others. No API key. The main source.
- **NASA Images** — public domain by US federal policy. Good for sky, space,
  earth and landscape concepts. No API key.
- **Wikimedia Commons** — used as a fallback for concepts the others cover
  poorly.

Provenance is recorded per image, not per batch: the creator, licence, source
and canonical URL of each photograph, written to ``provenance.json`` and a
human-readable ``CREDITS.md``. That is the same standard the library enforces at
ingest (D-012), and it is what makes the pool redistributable.

The pool is written outside the repository and is never committed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.generate import CONCEPTS  # noqa: E402

USER_AGENT = "voxframe-eval-fetch/0.1 (+https://github.com/Ngum12/voxframe)"

#: Smallest acceptable file. Below this is usually a thumbnail or an error page.
MIN_BYTES = 25_000

#: Politeness delay between API calls. These are free services with no key.
REQUEST_DELAY = 0.35

#: Search terms per concept. The concept key alone ("sun_sky") is a poor query;
#: these are written as a photographer would tag an image. Several concepts get
#: two phrasings so a thin result set for one does not starve the concept.
CONCEPT_QUERIES: dict[str, tuple[str, ...]] = {
    "sun_sky": ("bright sun in blue sky", "sunshine clear sky"),
    "moon_night": ("full moon night sky", "moon darkness"),
    "sunset_glow": ("orange sunset sky", "golden hour sky"),
    "storm_clouds": ("dark storm clouds", "thunderstorm sky"),
    "rain_weather": ("rain falling", "rainy weather drops"),
    "mountains_snow": ("snowy mountain peaks", "snow covered mountains"),
    "hills_green": ("green rolling hills", "grassy hillside landscape"),
    "forest_trees": ("pine forest trees", "dense woodland"),
    "farmland_fields": ("farm fields countryside", "agricultural field rows"),
    "desert_dunes": ("desert sand dunes", "sahara dunes"),
    "beach_sand": ("sandy beach shore", "beach coastline"),
    "ocean_waves": ("ocean waves sea", "breaking waves water"),
    "lake_calm": ("calm lake reflection", "still lake water"),
    "river_flowing": ("river flowing water", "winding river landscape"),
    "snow_winter": ("snow falling winter", "snowy winter scene"),
    "city_buildings": ("city skyline buildings", "urban skyscrapers"),
    "house_home": ("small house home", "cottage house exterior"),
    "bridge_span": ("bridge over water", "long bridge span"),
    "road_highway": ("empty road highway", "country road perspective"),
    "window_light": ("window light interior", "sunlight through window"),
    "book_reading": ("open book pages", "book reading"),
    "paper_document": ("paper document writing", "handwritten paper"),
    "clock_time": ("clock face time", "analog clock"),
    "key_lock": ("metal key", "old key lock"),
    "cup_drink": ("coffee cup", "cup of tea"),
    "flower_red": ("red flower blossom", "red rose flower"),
    "flower_yellow": ("yellow flower petals", "sunflower yellow"),
    "leaf_green": ("green leaf closeup", "single leaf branch"),
    "fire_flames": ("fire flames burning", "campfire flames"),
    "star_field": ("stars night sky", "milky way stars"),
    "bird_flying": ("bird flying sky", "bird in flight"),
    "fish_water": ("fish underwater", "fish swimming"),
    "tree_single": ("lone tree field", "single tree landscape"),
    "path_walking": ("forest path trail", "walking path grass"),
    "door_entrance": ("wooden door", "old door entrance"),
    "stairs_steps": ("stone steps stairs", "staircase steps"),
    "wheel_circle": ("wagon wheel", "bicycle wheel"),
    "box_container": ("wooden box crate", "cardboard box"),
    "bottle_glass": ("glass bottle", "bottle on table"),
    "candle_light": ("lit candle flame", "candle light"),
    "mountain_lake": ("mountain lake reflection", "alpine lake mountains"),
    "field_wheat": ("wheat field golden", "barley field harvest"),
    "cave_dark": ("cave entrance", "cave interior rock"),
    "cloud_white": ("white clouds blue sky", "fluffy clouds"),
    "ice_frozen": ("ice frozen surface", "ice crystals frozen"),
    "rock_stone": ("rocks stones boulder", "rocky surface stone"),
    "grass_meadow": ("meadow tall grass", "grass field meadow"),
    "sail_boat": ("sailboat water", "sailing boat sea"),
    "lamp_glow": ("lamp glowing light", "table lamp"),
    "chain_metal": ("metal chain links", "iron chain"),
}


@dataclass(frozen=True, slots=True)
class PhotoRecord:
    """One downloaded photograph, with everything needed to credit it."""

    concept: str
    filename: str
    title: str
    creator: str
    license_name: str
    license_url: str
    source: str
    source_url: str
    provider: str
    query: str
    width: int = 0
    height: int = 0


def _get_json(url: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def search_openverse(query: str, wanted: int) -> list[dict]:
    """Search Openverse for CC0 images.

    CC0 specifically, not merely "commercially usable": the pool should be
    redistributable without attribution obligations that a downstream user
    might miss.
    """
    url = (
        "https://api.openverse.org/v1/images/"
        f"?q={urllib.parse.quote(query)}"
        f"&license=cc0&page_size={min(wanted * 3, 40)}&mature=false"
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"    openverse: {type(exc).__name__}", file=sys.stderr)
        return []

    candidates = []
    for item in payload.get("results", []):
        if not item.get("url"):
            continue
        candidates.append(
            {
                "url": item["url"],
                "title": item.get("title") or "untitled",
                "creator": item.get("creator") or "Unknown",
                "license_name": (item.get("license") or "cc0").upper(),
                "license_url": item.get("license_url") or "",
                "source_url": item.get("foreign_landing_url") or item["url"],
                "provider": item.get("source") or "openverse",
                "source": "openverse",
            }
        )
    return candidates


def search_nasa(query: str, wanted: int) -> list[dict]:
    """Search NASA's image library.

    NASA media is public domain under US federal policy, with the documented
    exception of identifiable people and the NASA insignia — neither of which
    applies to the landscape and sky concepts this is used for.
    """
    url = (
        "https://images-api.nasa.gov/search"
        f"?q={urllib.parse.quote(query)}&media_type=image"
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"    nasa: {type(exc).__name__}", file=sys.stderr)
        return []

    candidates = []
    for item in payload.get("collection", {}).get("items", [])[: wanted * 3]:
        links = item.get("links") or []
        data = (item.get("data") or [{}])[0]
        if not links:
            continue
        candidates.append(
            {
                "url": links[0].get("href", ""),
                "title": data.get("title") or "untitled",
                "creator": data.get("center") or "NASA",
                "license_name": "Public Domain (NASA)",
                "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                "source_url": data.get("nasa_id", ""),
                "provider": "nasa",
                "source": "nasa",
            }
        )
    return [c for c in candidates if c["url"]]


def download(url: str, destination: Path) -> int:
    """Fetch one image. Returns bytes written, or 0 on failure."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0

    if len(data) < MIN_BYTES:
        return 0

    destination.write_bytes(data)
    return len(data)


def _dimensions(path: Path) -> tuple[int, int]:
    """Read image dimensions, or zeros if unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return (0, 0)


def write_provenance(records: list[PhotoRecord], directory: Path) -> None:
    """Write machine-readable and human-readable attribution.

    Both: the JSON drives ingest, the Markdown is what a person reads to check
    the pool is legitimately redistributable.
    """
    (directory / "provenance.json").write_text(
        json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8"
    )

    lines = [
        "# Evaluation photograph credits",
        "",
        f"{len(records)} photographs across "
        f"{len({r.concept for r in records})} concepts.",
        "",
        "Every image below is public domain or CC0 and may be redistributed.",
        "Generated by `scripts/fetch_eval_images.py`; do not edit by hand.",
        "",
    ]

    for concept in sorted({r.concept for r in records}):
        lines.append(f"## {concept}")
        lines.append("")
        for record in [r for r in records if r.concept == concept]:
            lines.append(
                f"- **{record.filename}** — {record.title} by {record.creator} "
                f"({record.license_name}, via {record.provider}) "
                f"{record.source_url}"
            )
        lines.append("")

    (directory / "CREDITS.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("TEMP", "/tmp")) / "voxframe_photo_pool",
        help="Where to write the pool. Outside the repo; never committed.",
    )
    parser.add_argument(
        "--per", type=int, default=6, help="Photographs per concept (default 6)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report availability, download nothing."
    )
    parser.add_argument(
        "--concepts", nargs="*", help="Limit to these concept keys."
    )
    args = parser.parse_args()

    concepts = [c.key for c in CONCEPTS]
    if args.concepts:
        concepts = [k for k in concepts if k in set(args.concepts)]

    try:
        import PIL  # noqa: F401
    except ImportError:
        print(
            "Note: Pillow is not installed, so downloaded images cannot "
            "be size-checked.\n"
            "      Install it for the full check:  pip install pillow\n"
        )

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Target: {args.per} photographs x {len(concepts)} concepts "
          f"= {args.per * len(concepts)}")
    print(f"Output: {args.out}\n")

    records: list[PhotoRecord] = []
    short: list[tuple[str, int]] = []

    for position, concept in enumerate(concepts, start=1):
        queries = CONCEPT_QUERIES.get(concept, (concept.replace("_", " "),))
        existing = sorted(args.out.glob(f"{concept}_*.jpg"))

        if len(existing) >= args.per:
            print(f"[{position:2}/{len(concepts)}] {concept:18} already have "
                  f"{len(existing)}")
            continue

        candidates: list[dict] = []
        for query in queries:
            candidates.extend(search_openverse(query, args.per))
            time.sleep(REQUEST_DELAY)
            if len(candidates) >= args.per * 2:
                break

        # NASA covers sky, space and landscape well and fills gaps where
        # Openverse's CC0 subset is thin.
        if len(candidates) < args.per:
            candidates.extend(search_nasa(queries[0], args.per))
            time.sleep(REQUEST_DELAY)

        if args.dry_run:
            print(f"[{position:2}/{len(concepts)}] {concept:18} "
                  f"{len(candidates)} candidates")
            if len(candidates) < args.per:
                short.append((concept, len(candidates)))
            continue

        saved = len(existing)
        seen_urls: set[str] = set()

        for candidate in candidates:
            if saved >= args.per:
                break
            if candidate["url"] in seen_urls:
                continue
            seen_urls.add(candidate["url"])

            filename = f"{concept}_{saved:02d}.jpg"
            path = args.out / filename

            if download(candidate["url"], path) == 0:
                continue

            width, height = _dimensions(path)
            # Zeros mean the size is unknown — Pillow absent, or a file it
            # cannot parse. Keep the download rather than discard it over a
            # check that could not run.
            if width and height and (width < 320 or height < 320):
                path.unlink(missing_ok=True)
                continue

            records.append(
                PhotoRecord(
                    concept=concept,
                    filename=filename,
                    title=candidate["title"][:120],
                    creator=candidate["creator"][:80],
                    license_name=candidate["license_name"],
                    license_url=candidate["license_url"],
                    source=candidate["source"],
                    source_url=candidate["source_url"][:300],
                    provider=candidate["provider"],
                    query=queries[0],
                    width=width,
                    height=height,
                )
            )
            saved += 1

        marker = "ok " if saved >= args.per else "LOW"
        print(f"[{position:2}/{len(concepts)}] {concept:18} {saved}/{args.per} {marker}")
        if saved < args.per:
            short.append((concept, saved))

    if args.dry_run:
        print(f"\nDry run complete. {len(short)} concept(s) below target.")
        return 0

    # Merge with any provenance from an earlier partial run.
    existing_file = args.out / "provenance.json"
    if existing_file.is_file():
        try:
            previous = json.loads(existing_file.read_text(encoding="utf-8"))
            known = {r.filename for r in records}
            records = [
                PhotoRecord(**p) for p in previous if p["filename"] not in known
            ] + records
        except (ValueError, TypeError):
            pass

    write_provenance(records, args.out)

    total = len(list(args.out.glob("*.jpg")))
    print(f"\n{total} photographs in {args.out}")
    print("Provenance: provenance.json and CREDITS.md")

    if short:
        print(f"\n{len(short)} concept(s) below target:")
        for concept, count in short:
            print(f"  {concept:20} {count}/{args.per}")
        print("Re-run to retry; already-downloaded files are kept.")

    print("\nNext:")
    print(f"  python scripts/run_large_eval.py --images {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
