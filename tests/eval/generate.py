"""Generate a synthetic evaluation pool — a FAST REGRESSION TEST.

**This pool does not justify model choices.** It is flat vector art with one
characteristic hue per concept, while CLIP is trained on photographs (D-043).
Use it to catch regressions quickly and offline; use the real-photograph pool
from ``scripts/fetch_eval_images.py`` to decide anything.

Its virtues are speed and determinism: 400 images generated in a few seconds,
identical on every machine, with no download and no licence question. That
makes it suitable for CI and for checking that a change did not break
retrieval. It is not suitable for comparing encoders, because a model that
matched on dominant colour alone would score well here.

The Phase 3 eval originally used 12 images and 12 cases, where one case was 8
percentage points — far too coarse to separate models. This builds 400 images
and 50 queries per language, which is enough to see a regression.

What makes a distractor hard
----------------------------
A pool of obviously different images measures almost nothing: any working
encoder separates a sun from a city. Three kinds of difficulty are built in
deliberately:

1. **Near-duplicates.** The same concept re-rendered with small variations in
   palette, position and scale. Retrieval must pick the right one, and the
   eval's "acceptable" set records where a sibling is a fair answer.
2. **Confusable pairs.** Concepts that genuinely look alike as simple graphics:
   a sun over dunes versus a moon over hills, a green forest versus green
   farmland. These are where the earlier eval's single shared failure
   (``sun → desert_sand``) came from, so they are now systematic rather than
   accidental.
3. **Scale.** Many concepts mean the correct image competes against 300 rather
   than 11, so top-1 becomes a meaningfully harder task.

Images remain generated rather than downloaded: offline, deterministic, no
licensing questions. Absolute scores will still exceed what photographs give,
but the eval exists to *compare* encoders and catch regressions.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageDraw

# Pillow is imported inside the drawing functions rather than at module level.
# `scripts/fetch_eval_images.py` imports this module only for CONCEPTS, and
# that script must run on a fresh install where the optional image extras are
# not present.

__all__ = ["CONCEPTS", "POOL_SIZE", "Concept", "generate_large_pool"]

#: Variants per concept. Eight gives strong near-duplicate pressure and brings
#: the pool past 300 images, which is where top-1 becomes a demanding task
#: rather than a coin toss between a handful of obviously different pictures.
VARIANTS_PER_CONCEPT = 8

IMAGE_SIZE = 384


@dataclass(frozen=True, slots=True)
class Concept:
    """A visual concept with the text that should retrieve it.

    Attributes:
        key: Identifier.
        text_en: Narration-style English text, as the pipeline would receive.
        text_fr: The same meaning in natural French.
        hue: Base hue, 0-1. Variants shift around it.
        scheme: Which drawing routine renders it.
        confusable: Concepts that look similar enough to be fair alternatives.
    """

    key: str
    text_en: str
    text_fr: str
    hue: float
    scheme: str
    confusable: tuple[str, ...] = ()


#: Concepts are grouped so that confusable pairs sit together, which is what
#: makes the pool discriminating rather than merely large.
CONCEPTS: tuple[Concept, ...] = (
    # --- sky and light: sun/moon are the classic confusion ---
    Concept("sun_sky", "The bright sun rose over the horizon",
            "Le soleil brillant se leva sur l'horizon", 0.13, "orb_sky",
            ("desert_dunes", "moon_night")),
    Concept("moon_night", "The pale moon hung among the stars at night",
            "La lune pâle flottait parmi les étoiles la nuit", 0.62, "orb_night",
            ("sun_sky",)),
    Concept("sunset_glow", "The sky glowed orange as the sun set",
            "Le ciel rougeoyait orange au coucher du soleil", 0.06, "gradient_sky",
            ("sun_sky", "fire_flames")),
    Concept("storm_clouds", "Dark storm clouds gathered overhead",
            "De sombres nuages d'orage s'amoncelaient", 0.60, "clouds",
            ("rain_weather",)),
    Concept("rain_weather", "Rain fell steadily from a grey sky",
            "La pluie tombait sans cesse d'un ciel gris", 0.55, "rain",
            ("storm_clouds",)),

    # --- landscape: several greens that must not collapse together ---
    Concept("mountains_snow", "Snow covered mountain peaks rose above the valley",
            "Les sommets enneigés dominaient la vallée", 0.55, "peaks",
            ("snow_winter", "hills_green")),
    Concept("hills_green", "Rolling green hills stretched into the distance",
            "Des collines verdoyantes s'étendaient au loin", 0.30, "hills",
            ("farmland_fields", "mountains_snow")),
    Concept("forest_trees", "A thick forest of tall pine trees",
            "Une forêt dense de grands pins", 0.35, "trees",
            ("hills_green", "farmland_fields")),
    Concept("farmland_fields", "Green farm fields divided by hedgerows",
            "Des champs verts séparés par des haies", 0.25, "fields",
            ("hills_green", "forest_trees")),
    Concept("desert_dunes", "Sand dunes stretched across the hot desert",
            "Les dunes de sable s'étendaient dans le désert brûlant", 0.10, "dunes",
            ("sun_sky", "beach_sand")),
    Concept("beach_sand", "A sandy beach beside the sea",
            "Une plage de sable au bord de la mer", 0.12, "beach",
            ("desert_dunes", "ocean_waves")),
    Concept("ocean_waves", "Waves rolled across the deep blue ocean",
            "Les vagues déferlaient sur l'océan bleu profond", 0.57, "waves",
            ("lake_calm", "beach_sand")),
    Concept("lake_calm", "A still lake reflecting the sky",
            "Un lac calme reflétant le ciel", 0.52, "lake",
            ("ocean_waves",)),
    Concept("river_flowing", "A river winding through the landscape",
            "Une rivière serpentant à travers le paysage", 0.50, "river",
            ("lake_calm",)),
    Concept("snow_winter", "Snow fell softly through the winter air",
            "La neige tombait doucement dans l'air d'hiver", 0.58, "snowfall",
            ("mountains_snow",)),

    # --- built environment ---
    Concept("city_buildings", "Tall buildings towered over the city streets",
            "De grands immeubles dominaient les rues de la ville", 0.60, "skyline",
            ("bridge_span", "house_home")),
    Concept("house_home", "A small house with a pitched roof",
            "Une petite maison au toit pentu", 0.08, "house",
            ("city_buildings",)),
    Concept("bridge_span", "A long bridge crossing the water",
            "Un long pont traversant l'eau", 0.58, "bridge",
            ("city_buildings", "road_highway")),
    Concept("road_highway", "A road stretching far into the distance",
            "Une route s'étirant loin dans le lointain", 0.0, "road",
            ("bridge_span",)),
    Concept("window_light", "Light coming through a tall window",
            "La lumière entrant par une haute fenêtre", 0.15, "window", ()),

    # --- objects ---
    Concept("book_reading", "An open book with printed pages",
            "Un livre ouvert aux pages imprimées", 0.09, "book",
            ("paper_document",)),
    Concept("paper_document", "A sheet of paper covered in writing",
            "Une feuille de papier couverte d'écriture", 0.11, "document",
            ("book_reading",)),
    Concept("clock_time", "A clock face showing the hour",
            "Une horloge indiquant l'heure", 0.0, "clock", ()),
    Concept("key_lock", "A metal key for a lock",
            "Une clé en métal pour une serrure", 0.14, "key", ()),
    Concept("cup_drink", "A cup of hot coffee",
            "Une tasse de café chaud", 0.07, "cup", ()),

    # --- nature detail ---
    Concept("flower_red", "A red flower blooming in the garden",
            "Une fleur rouge s'épanouissant dans le jardin", 0.99, "flower",
            ("flower_yellow",)),
    Concept("flower_yellow", "A yellow flower with wide petals",
            "Une fleur jaune aux larges pétales", 0.15, "flower",
            ("flower_red", "sun_sky")),
    Concept("leaf_green", "A single green leaf on a branch",
            "Une feuille verte sur une branche", 0.28, "leaf",
            ("forest_trees",)),
    Concept("fire_flames", "Orange flames burning in the dark",
            "Des flammes orange brûlant dans l'obscurité", 0.04, "flames",
            ("sunset_glow",)),
    Concept("star_field", "Countless stars scattered across space",
            "D'innombrables étoiles dispersées dans l'espace", 0.70, "stars",
            ("moon_night",)),
    # --- additional concepts, to bring queries past 50 per language ---
    Concept("bird_flying", "A bird flying across the open sky",
            "Un oiseau volant dans le ciel ouvert", 0.58, "bird",
            ("star_field",)),
    Concept("fish_water", "A fish swimming beneath the surface",
            "Un poisson nageant sous la surface", 0.54, "fish",
            ("ocean_waves",)),
    Concept("tree_single", "A single tree standing alone in a field",
            "Un arbre isolé debout dans un champ", 0.32, "lone_tree",
            ("forest_trees", "farmland_fields")),
    Concept("path_walking", "A narrow path leading through the grass",
            "Un sentier étroit traversant l'herbe", 0.26, "path",
            ("road_highway",)),
    Concept("door_entrance", "A wooden door set into a wall",
            "Une porte en bois dans un mur", 0.08, "door",
            ("window_light", "house_home")),
    Concept("stairs_steps", "A flight of stone steps rising upward",
            "Un escalier de pierre montant vers le haut", 0.1, "stairs", ()),
    Concept("wheel_circle", "A round wheel with spokes",
            "Une roue ronde à rayons", 0.05, "wheel",
            ("clock_time",)),
    Concept("box_container", "A closed wooden box",
            "Une boîte en bois fermée", 0.09, "box", ()),
    Concept("bottle_glass", "A tall glass bottle",
            "Une grande bouteille en verre", 0.4, "bottle",
            ("cup_drink",)),
    Concept("candle_light", "A lit candle burning quietly",
            "Une bougie allumée brûlant doucement", 0.12, "candle",
            ("fire_flames",)),
    Concept("mountain_lake", "A lake surrounded by mountains",
            "Un lac entouré de montagnes", 0.53, "mountain_lake",
            ("lake_calm", "mountains_snow")),
    Concept("field_wheat", "A field of golden wheat",
            "Un champ de blé doré", 0.14, "wheat",
            ("farmland_fields", "desert_dunes")),
    Concept("cave_dark", "A dark cave opening in the rock",
            "Une grotte sombre ouverte dans la roche", 0.07, "cave", ()),
    Concept("cloud_white", "White clouds drifting in a blue sky",
            "Des nuages blancs dérivant dans un ciel bleu", 0.56, "white_clouds",
            ("storm_clouds",)),
    Concept("ice_frozen", "A sheet of blue ice",
            "Une plaque de glace bleue", 0.52, "ice",
            ("snow_winter",)),
    Concept("rock_stone", "Large grey rocks piled together",
            "De grosses pierres grises empilées", 0.0, "rocks",
            ("mountains_snow", "cave_dark")),
    Concept("grass_meadow", "An open meadow of tall grass",
            "Une prairie ouverte d'herbes hautes", 0.27, "meadow",
            ("hills_green", "field_wheat")),
    Concept("sail_boat", "A small boat with a white sail",
            "Un petit bateau à voile blanche", 0.55, "boat",
            ("ocean_waves", "lake_calm")),
    Concept("lamp_glow", "A lamp casting warm light",
            "Une lampe diffusant une lumière chaude", 0.13, "lamp",
            ("candle_light", "window_light")),
    Concept("chain_metal", "Links of a heavy metal chain",
            "Les maillons d'une lourde chaîne métallique", 0.6, "chain",
            ("key_lock",)),
)


POOL_SIZE = len(CONCEPTS) * VARIANTS_PER_CONCEPT


def _hex(hue: float, saturation: float, value: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _draw(
    draw: ImageDraw.ImageDraw, scheme: str, hue: float, variant: int, size: int
) -> None:
    """Render one concept variant.

    ``variant`` shifts hue, position and scale slightly, producing
    near-duplicates that a retrieval model must still rank sensibly.
    """
    centred = variant - (VARIANTS_PER_CONCEPT - 1) / 2
    shift = centred * 0.015
    scale = 1.0 + centred * 0.035
    nudge = centred * 0.018
    h = hue + shift

    def circle(cx: float, cy: float, r: float, colour: str) -> None:
        x, y, rr = cx * size, cy * size, r * size * scale
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=colour)

    def rect(x: float, y: float, w: float, ht: float, colour: str) -> None:
        draw.rectangle(
            [x * size, y * size, (x + w) * size, (y + ht) * size], fill=colour
        )

    def triangle(cx: float, base: float, half: float, height: float, colour: str) -> None:
        draw.polygon(
            [
                ((cx - half * scale) * size, base * size),
                ((cx + half * scale) * size, base * size),
                (cx * size, (base - height * scale) * size),
            ],
            fill=colour,
        )

    def band(y: float, amplitude: float, colour: str, cycles: int = 3) -> None:
        points = []
        for step in range(65):
            f = step / 64
            points.append(
                (f * size, (y + amplitude * math.sin(f * math.pi * cycles)) * size)
            )
        points += [(size, size), (0, size)]
        draw.polygon(points, fill=colour)

    if scheme == "orb_sky":
        circle(0.5 + nudge, 0.35, 0.16, _hex(h, 0.85, 0.98))
    elif scheme == "orb_night":
        circle(0.62 + nudge, 0.3, 0.12, _hex(h, 0.05, 0.95))
        for i in range(7):
            circle(0.1 + i * 0.13, 0.15 + (i % 3) * 0.2, 0.008, "#ffffff")
    elif scheme == "gradient_sky":
        for i in range(8):
            rect(0, i / 8, 1, 1 / 8 + 0.01, _hex(h + i * 0.012, 0.8, 0.95 - i * 0.07))
    elif scheme == "clouds":
        for i in range(4):
            circle(0.2 + i * 0.2 + nudge, 0.35 + (i % 2) * 0.1, 0.14, _hex(h, 0.15, 0.45))
    elif scheme == "rain":
        for i in range(24):
            x = (i * 0.041 + nudge) % 1.0
            y = (i * 0.17) % 0.9
            rect(x, y, 0.006, 0.07, _hex(h, 0.35, 0.85))
    elif scheme == "peaks":
        triangle(0.32 + nudge, 0.88, 0.26, 0.52, _hex(h, 0.25, 0.48))
        triangle(0.66 + nudge, 0.88, 0.28, 0.6, _hex(h, 0.2, 0.55))
        triangle(0.32 + nudge, 0.46, 0.09, 0.12, "#ffffff")
        triangle(0.66 + nudge, 0.38, 0.1, 0.13, "#ffffff")
    elif scheme == "hills":
        band(0.62, 0.07, _hex(h, 0.55, 0.62), 2)
        band(0.78, 0.06, _hex(h, 0.6, 0.48), 3)
    elif scheme == "trees":
        for i in range(4):
            triangle(0.16 + i * 0.23 + nudge, 0.9, 0.11, 0.42 + (i % 2) * 0.12,
                     _hex(h, 0.65, 0.4 + (i % 2) * 0.12))
    elif scheme == "fields":
        for i in range(4):
            rect(0, 0.45 + i * 0.14, 1, 0.12, _hex(h + i * 0.02, 0.5, 0.55 + i * 0.06))
    elif scheme == "dunes":
        band(0.58, 0.1, _hex(h, 0.55, 0.78), 2)
        band(0.78, 0.09, _hex(h, 0.6, 0.62), 3)
    elif scheme == "beach":
        rect(0, 0, 1, 0.55, _hex(0.55, 0.45, 0.85))
        band(0.6, 0.04, _hex(h, 0.35, 0.92), 4)
    elif scheme == "waves":
        for i in range(3):
            band(0.5 + i * 0.16, 0.06, _hex(h, 0.75 - i * 0.12, 0.55 + i * 0.13), 4)
    elif scheme == "lake":
        # Shoreline, still water, and horizontal reflection streaks. A flat
        # band with one line did not read as a lake at all.
        rect(0, 0, 1, 0.48, _hex(h - 0.04, 0.35, 0.78))
        band(0.48, 0.02, _hex(0.28, 0.45, 0.45), 2)
        rect(0, 0.52, 1, 0.48, _hex(h, 0.55, 0.62))
        for i in range(5):
            rect(0.15 + (i % 3) * 0.06, 0.58 + i * 0.07, 0.5 - (i % 3) * 0.12, 0.015,
                 _hex(h, 0.25, 0.88))
    elif scheme == "river":
        points = [((0.35 + 0.18 * math.sin(i / 3)) * size, i / 10 * size) for i in range(11)]
        points += [((0.55 + 0.18 * math.sin(i / 3)) * size, i / 10 * size)
                   for i in range(10, -1, -1)]
        draw.polygon(points, fill=_hex(h, 0.55, 0.68))
    elif scheme == "snowfall":
        for i in range(22):
            circle((i * 0.091 + nudge) % 1.0, (i * 0.13) % 0.95, 0.016, "#ffffff")
    elif scheme == "skyline":
        for i in range(5):
            rect(0.06 + i * 0.19, 0.2 + (i % 3) * 0.12, 0.13,
                 0.8 - (i % 3) * 0.12, _hex(h, 0.25, 0.3 + (i % 3) * 0.1))
    elif scheme == "house":
        rect(0.28, 0.5, 0.44, 0.38, _hex(h, 0.45, 0.8))
        triangle(0.5, 0.5, 0.3, 0.22, _hex(h, 0.7, 0.55))
        rect(0.45, 0.68, 0.1, 0.2, _hex(h, 0.8, 0.35))
    elif scheme == "bridge":
        rect(0, 0.52, 1, 0.06, _hex(h, 0.2, 0.55))
        for i in range(4):
            rect(0.14 + i * 0.24, 0.58, 0.05, 0.3, _hex(h, 0.2, 0.42))
    elif scheme == "road":
        draw.polygon([(0.5 * size, 0.3 * size), (1.0 * size, size), (0, size)],
                     fill=_hex(h, 0.02, 0.38))
        for i in range(4):
            rect(0.49, 0.42 + i * 0.16, 0.02, 0.08, _hex(0.14, 0.85, 0.9))
    elif scheme == "window":
        rect(0.3, 0.18, 0.4, 0.62, _hex(h, 0.25, 0.95))
        rect(0.49, 0.18, 0.02, 0.62, _hex(h, 0.3, 0.4))
        rect(0.3, 0.46, 0.4, 0.02, _hex(h, 0.3, 0.4))
    elif scheme == "book":
        rect(0.14, 0.34, 0.34, 0.34, _hex(h, 0.07, 0.97))
        rect(0.52, 0.34, 0.34, 0.34, _hex(h, 0.1, 0.92))
        rect(0.485, 0.34, 0.03, 0.34, _hex(h, 0.55, 0.45))
        for i in range(5):
            rect(0.18, 0.4 + i * 0.05, 0.26, 0.012, _hex(h, 0.2, 0.55))
    elif scheme == "document":
        rect(0.26, 0.16, 0.48, 0.68, "#fdfdf8")
        for i in range(9):
            rect(0.31, 0.24 + i * 0.065, 0.36 - (i % 3) * 0.08, 0.016, _hex(h, 0.15, 0.4))
    elif scheme == "clock":
        circle(0.5, 0.5, 0.32, _hex(h, 0.06, 0.95))
        circle(0.5, 0.5, 0.28, _hex(h, 0.03, 0.99))
        rect(0.49, 0.28, 0.02, 0.24, _hex(h, 0.1, 0.2))
        rect(0.5, 0.49, 0.18, 0.02, _hex(h, 0.1, 0.2))
    elif scheme == "key":
        circle(0.3, 0.5, 0.13, _hex(h, 0.35, 0.78))
        circle(0.3, 0.5, 0.06, _hex(h, 0.1, 0.25))
        rect(0.4, 0.47, 0.34, 0.06, _hex(h, 0.35, 0.78))
        rect(0.62, 0.53, 0.05, 0.1, _hex(h, 0.35, 0.78))
    elif scheme == "cup":
        rect(0.3, 0.4, 0.34, 0.34, _hex(h, 0.12, 0.96))
        circle(0.69, 0.53, 0.09, _hex(h, 0.12, 0.96))
        circle(0.69, 0.53, 0.05, _hex(h, 0.3, 0.55))
        rect(0.33, 0.43, 0.28, 0.06, _hex(h, 0.8, 0.35))
    elif scheme == "flower":
        for angle in range(0, 360, 72):
            radians = math.radians(angle)
            circle(0.5 + 0.13 * math.cos(radians), 0.48 + 0.13 * math.sin(radians),
                   0.1, _hex(h, 0.75, 0.9))
        circle(0.5, 0.48, 0.07, _hex(0.13, 0.9, 0.95))
    elif scheme == "leaf":
        draw.polygon(
            [(0.5 * size, 0.18 * size), (0.72 * size, 0.5 * size),
             (0.5 * size, 0.84 * size), (0.28 * size, 0.5 * size)],
            fill=_hex(h, 0.7, 0.6),
        )
        rect(0.49, 0.18, 0.02, 0.66, _hex(h, 0.5, 0.35))
    elif scheme == "flames":
        triangle(0.5 + nudge, 0.92, 0.28, 0.54, _hex(h, 0.9, 0.85))
        triangle(0.5 + nudge, 0.9, 0.17, 0.38, _hex(h + 0.04, 0.85, 0.95))
        triangle(0.5 + nudge, 0.85, 0.08, 0.2, _hex(h + 0.09, 0.5, 1.0))
    elif scheme == "bird":
        # Two swept wings plus a body: the silhouette people recognise, rather
        # than a chevron that reads as an arrow.
        body = _hex(h, 0.35, 0.22)
        draw.polygon([(0.5*size,0.46*size),(0.14*size,0.3*size),(0.2*size,0.4*size),
                      (0.42*size,0.52*size)], fill=body)
        draw.polygon([(0.5*size,0.46*size),(0.86*size,0.3*size),(0.8*size,0.4*size),
                      (0.58*size,0.52*size)], fill=body)
        draw.ellipse([0.44*size,0.42*size,0.58*size,0.58*size], fill=body)
        circle(0.58, 0.44, 0.03, body)
    elif scheme == "fish":
        circle(0.48, 0.5, 0.17, _hex(h, 0.6, 0.8))
        draw.polygon([(0.66*size,0.5*size),(0.82*size,0.38*size),(0.82*size,0.62*size)],
                     fill=_hex(h,0.65,0.7))
        circle(0.38, 0.46, 0.022, "#101018")
    elif scheme == "lone_tree":
        rect(0.47, 0.55, 0.06, 0.33, _hex(0.08, 0.6, 0.4))
        circle(0.5 + nudge, 0.42, 0.22, _hex(h, 0.65, 0.5))
    elif scheme == "path":
        top = 0.5 + nudge
        draw.polygon([((top-0.06)*size, 0.3*size), ((top+0.06)*size, 0.3*size),
                      ((0.8+nudge)*size, size), ((0.2+nudge)*size, size)],
                     fill=_hex(0.1 + shift, 0.35, 0.78))
    elif scheme == "door":
        rect(0.32, 0.22, 0.36, 0.66, _hex(h, 0.65, 0.5))
        rect(0.36, 0.27, 0.28, 0.25, _hex(h, 0.55, 0.62))
        circle(0.62, 0.58, 0.026, _hex(0.13, 0.7, 0.85))
    elif scheme == "stairs":
        for i in range(5):
            rect(0.12 + i * 0.05, 0.82 - i * 0.13, 0.7 - i * 0.08, 0.1,
                 _hex(h, 0.12, 0.72 - i * 0.06))
    elif scheme == "wheel":
        circle(0.5, 0.5, 0.33, _hex(h, 0.3, 0.45))
        circle(0.5, 0.5, 0.26, _hex(h, 0.2, 0.7))
        for angle in range(0, 360, 45):
            r = math.radians(angle)
            draw.line([(0.5*size, 0.5*size),
                       ((0.5+0.26*math.cos(r))*size, (0.5+0.26*math.sin(r))*size)],
                      fill=_hex(h,0.3,0.4), width=max(2, size//96))
        circle(0.5, 0.5, 0.06, _hex(h, 0.4, 0.35))
    elif scheme == "box":
        rect(0.24, 0.36, 0.52, 0.42, _hex(h, 0.55, 0.62))
        rect(0.24, 0.36, 0.52, 0.08, _hex(h, 0.6, 0.48))
        rect(0.47, 0.44, 0.06, 0.34, _hex(h, 0.5, 0.42))
    elif scheme == "bottle":
        rect(0.42, 0.16, 0.16, 0.16, _hex(h, 0.45, 0.72))
        rect(0.34, 0.32, 0.32, 0.5, _hex(h, 0.5, 0.78))
        rect(0.4, 0.44, 0.2, 0.16, _hex(h, 0.2, 0.92))
    elif scheme == "candle":
        rect(0.44, 0.44, 0.12, 0.4, _hex(0.1, 0.15, 0.96))
        triangle(0.5, 0.44, 0.045, 0.16, _hex(h, 0.85, 0.98))
        triangle(0.5, 0.4, 0.02, 0.08, _hex(h + 0.06, 0.4, 1.0))
    elif scheme == "mountain_lake":
        triangle(0.3 + nudge, 0.58, 0.24, 0.4, _hex(h, 0.28, 0.5))
        triangle(0.66 + nudge, 0.58, 0.26, 0.46, _hex(h, 0.22, 0.58))
        rect(0, 0.58, 1, 0.42, _hex(h, 0.5, 0.62))
    elif scheme == "wheat":
        for i in range(14):
            x = 0.05 + i * 0.068
            rect(x, 0.42, 0.012, 0.46, _hex(h, 0.6, 0.72))
            circle(x + 0.006, 0.4, 0.028, _hex(h, 0.7, 0.88))
    elif scheme == "cave":
        # An arched opening in a lighter rock face. A bare dark triangle was
        # indistinguishable from a mountain.
        rect(0, 0, 1, 1, _hex(h, 0.18, 0.42))
        draw.pieslice([0.24*size, 0.3*size, 0.76*size, 0.82*size], 180, 360,
                      fill="#0b0b11")
        rect(0.24, 0.56, 0.52, 0.32, "#0b0b11")
        for cx, cy, r in ((0.16, 0.72, 0.09), (0.86, 0.78, 0.1)):
            circle(cx, cy, r, _hex(h, 0.12, 0.34))
    elif scheme == "white_clouds":
        for i in range(5):
            circle(0.16 + i * 0.17 + nudge, 0.4 + (i % 2) * 0.12, 0.13, "#fafcff")
    elif scheme == "ice":
        for i in range(6):
            draw.polygon([((0.1+i*0.15)*size, 0.3*size), ((0.22+i*0.15)*size, 0.42*size),
                          ((0.14+i*0.15)*size, 0.66*size), ((0.04+i*0.15)*size, 0.5*size)],
                         fill=_hex(h, 0.25 + (i % 3) * 0.08, 0.92))
    elif scheme == "rocks":
        for cx, cy, r in ((0.32, 0.68, 0.17), (0.6, 0.72, 0.2), (0.48, 0.52, 0.14)):
            circle(cx + nudge, cy, r, _hex(h, 0.05, 0.42 + r))
    elif scheme == "meadow":
        rect(0, 0.5, 1, 0.5, _hex(h, 0.55, 0.62))
        for i in range(26):
            x = (i * 0.038 + nudge) % 1.0
            rect(x, 0.52 + (i % 4) * 0.02, 0.008, 0.16, _hex(h, 0.65, 0.48))
    elif scheme == "boat":
        cx = 0.5 + nudge
        rect(0, 0.62, 1, 0.38, _hex(0.55 + shift, 0.5, 0.6))
        draw.polygon([((cx-0.2)*size,0.62*size),((cx+0.2)*size,0.62*size),
                      ((cx+0.12)*size,0.74*size),((cx-0.12)*size,0.74*size)],
                     fill=_hex(0.08, 0.55, 0.45 + shift))
        draw.polygon([(cx*size,(0.24 - shift)*size),(cx*size,0.6*size),
                      ((cx+0.18)*size,0.6*size)], fill="#fbfbfb")
    elif scheme == "lamp":
        draw.polygon([(0.34*size,0.5*size),(0.66*size,0.5*size),(0.58*size,0.28*size),
                      (0.42*size,0.28*size)], fill=_hex(h,0.55,0.75))
        circle(0.5, 0.58, 0.1, _hex(h, 0.35, 0.98))
        rect(0.48, 0.58, 0.04, 0.28, _hex(h, 0.3, 0.42))
    elif scheme == "chain":
        for i in range(5):
            x = 0.16 + i * 0.16
            draw.ellipse([x*size, (0.42+ (i%2)*0.06)*size,
                          (x+0.14)*size, (0.6+(i%2)*0.06)*size],
                         outline=_hex(h,0.12,0.62), width=max(3, size//64))
    elif scheme == "stars":
        for i in range(40):
            x = ((i * 0.618) % 1.0)
            y = ((i * 0.382) % 1.0)
            circle(x, y, 0.006 + (i % 4) * 0.003, "#ffffff")


def generate_large_pool(directory: Path, size: int = IMAGE_SIZE) -> dict[str, Path]:
    """Generate the full pool.

    Returns:
        Mapping of ``"{concept}_v{n}"`` to path.
    """
    from PIL import Image, ImageDraw, ImageFilter

    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for concept in CONCEPTS:
        for variant in range(VARIANTS_PER_CONCEPT):
            key = f"{concept.key}_v{variant}"
            path = directory / f"{key}.png"
            paths[key] = path

            if path.exists():
                continue

            background = {
                "orb_sky": _hex(0.56, 0.55, 0.88),
                "orb_night": "#0a0e1e",
                "stars": "#05070f",
                "flames": "#140a06",
                "snowfall": "#dfe8f2",
                "beach": _hex(0.55, 0.45, 0.85),
            }.get(concept.scheme, _hex(concept.hue, 0.3, 0.62))

            image = Image.new("RGB", (size, size), background)
            _draw(ImageDraw.Draw(image), concept.scheme, concept.hue, variant, size)
            image.filter(ImageFilter.GaussianBlur(radius=0.8)).save(path, "PNG")

    return paths
