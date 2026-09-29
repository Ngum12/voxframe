"""A small labelled evaluation set for text-to-image retrieval.

Built before the matcher (project owner's instruction), so encoder choices are
decided by measurement rather than by reading model cards. It also serves as a
regression guard: a change that improves English while breaking French shows up
here immediately.

Design
------
Each case pairs a **scene text** in English and French with one **correct**
image and several **distractor** images. Retrieval runs over the whole image
pool, so a case with 12 images in the pool is a 1-in-12 task, not 1-in-3.

Images are generated, not downloaded. That keeps the eval runnable offline with
no licensing questions, and makes it deterministic. The cost is that generated
images are simpler than photographs, so absolute scores here will be higher
than on a real library — which is fine, because the eval exists to *compare*
encoders and catch regressions, not to predict field accuracy.

The concepts are chosen to be visually distinguishable by shape and colour
alone, since that is what a generated image can express.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["EVAL_CASES", "IMAGE_POOL", "EvalCase", "ImageSpec"]


@dataclass(frozen=True, slots=True)
class ImageSpec:
    """A generated test image.

    Attributes:
        key: Identifier, used as the filename stem.
        description: What the image depicts, in plain English. Used to build
            the image via drawing primitives, not passed to any model.
        background: Background colour, hex.
        shapes: Drawing instructions: ``(kind, colour, relative geometry)``.
    """

    key: str
    description: str
    background: str
    shapes: tuple[tuple[str, str, tuple[float, ...]], ...] = field(default=())


#: The image pool. Every case retrieves against all of these.
IMAGE_POOL: tuple[ImageSpec, ...] = (
    ImageSpec(
        key="sun_sky",
        description="a bright yellow sun in a clear blue sky",
        background="#4A9FE0",
        shapes=(("circle", "#FFD93D", (0.5, 0.35, 0.16)),),
    ),
    ImageSpec(
        key="night_moon",
        description="a white moon in a dark night sky with stars",
        background="#12172B",
        shapes=(
            ("circle", "#F0F0E8", (0.66, 0.28, 0.11)),
            ("circle", "#FFFFFF", (0.22, 0.20, 0.012)),
            ("circle", "#FFFFFF", (0.38, 0.42, 0.010)),
            ("circle", "#FFFFFF", (0.80, 0.58, 0.011)),
        ),
    ),
    ImageSpec(
        key="mountains_snow",
        description="snow capped mountain peaks",
        background="#8FB8DE",
        shapes=(
            ("triangle", "#5A6B7C", (0.30, 0.85, 0.28, 0.55)),
            ("triangle", "#6E7F90", (0.62, 0.85, 0.32, 0.62)),
            ("triangle", "#FFFFFF", (0.30, 0.44, 0.10, 0.14)),
            ("triangle", "#FFFFFF", (0.62, 0.36, 0.11, 0.15)),
        ),
    ),
    ImageSpec(
        key="ocean_waves",
        description="deep blue ocean water with waves",
        background="#0F5E8C",
        shapes=(
            ("wave", "#2E86C1", (0.0, 0.55, 1.0, 0.08)),
            ("wave", "#5DADE2", (0.0, 0.72, 1.0, 0.07)),
            ("wave", "#85C1E9", (0.0, 0.88, 1.0, 0.06)),
        ),
    ),
    ImageSpec(
        key="forest_trees",
        description="green pine trees in a forest",
        background="#3E6B4F",
        shapes=(
            ("triangle", "#1E4D2B", (0.22, 0.88, 0.14, 0.50)),
            ("triangle", "#276B38", (0.50, 0.88, 0.16, 0.58)),
            ("triangle", "#1E4D2B", (0.78, 0.88, 0.14, 0.46)),
        ),
    ),
    ImageSpec(
        key="desert_sand",
        description="orange sand dunes in a hot desert",
        background="#E3A857",
        shapes=(
            ("wave", "#C88B42", (0.0, 0.62, 1.0, 0.12)),
            ("wave", "#B37934", (0.0, 0.82, 1.0, 0.12)),
            ("circle", "#FFE9A8", (0.74, 0.22, 0.08)),
        ),
    ),
    ImageSpec(
        key="fire_flames",
        description="orange and red flames of a burning fire",
        background="#1A0E08",
        shapes=(
            ("triangle", "#E2521A", (0.50, 0.92, 0.30, 0.55)),
            ("triangle", "#F5A623", (0.50, 0.90, 0.18, 0.38)),
            ("triangle", "#FFE066", (0.50, 0.86, 0.09, 0.22)),
        ),
    ),
    ImageSpec(
        key="snow_winter",
        description="white snow falling in winter",
        background="#DCE6F0",
        shapes=(
            ("circle", "#FFFFFF", (0.20, 0.25, 0.030)),
            ("circle", "#FFFFFF", (0.45, 0.15, 0.025)),
            ("circle", "#FFFFFF", (0.70, 0.32, 0.032)),
            ("circle", "#FFFFFF", (0.32, 0.58, 0.027)),
            ("circle", "#FFFFFF", (0.62, 0.70, 0.029)),
            ("circle", "#FFFFFF", (0.85, 0.52, 0.024)),
        ),
    ),
    ImageSpec(
        key="city_buildings",
        description="tall grey city buildings and skyscrapers",
        background="#6C7A89",
        shapes=(
            ("rect", "#34495E", (0.12, 0.40, 0.14, 0.60)),
            ("rect", "#2C3E50", (0.32, 0.25, 0.16, 0.75)),
            ("rect", "#3D566E", (0.55, 0.35, 0.13, 0.65)),
            ("rect", "#283747", (0.74, 0.20, 0.15, 0.80)),
        ),
    ),
    ImageSpec(
        key="flower_red",
        description="a red flower blossom with petals",
        background="#8FBF6F",
        shapes=(
            ("circle", "#D64541", (0.50, 0.42, 0.10)),
            ("circle", "#E8635F", (0.36, 0.42, 0.075)),
            ("circle", "#E8635F", (0.64, 0.42, 0.075)),
            ("circle", "#E8635F", (0.50, 0.28, 0.075)),
            ("circle", "#E8635F", (0.50, 0.56, 0.075)),
            ("circle", "#F5C518", (0.50, 0.42, 0.045)),
        ),
    ),
    ImageSpec(
        key="road_highway",
        description="a long grey road stretching into the distance",
        background="#7F8C8D",
        shapes=(
            ("triangle", "#4D5656", (0.50, 1.00, 0.90, 0.70)),
            ("rect", "#F4D03F", (0.49, 0.45, 0.02, 0.20)),
            ("rect", "#F4D03F", (0.49, 0.72, 0.02, 0.18)),
        ),
    ),
    ImageSpec(
        key="book_reading",
        description="an open book with white pages",
        background="#A0724A",
        shapes=(
            ("rect", "#FAF3E0", (0.18, 0.35, 0.30, 0.34)),
            ("rect", "#F0E6D2", (0.52, 0.35, 0.30, 0.34)),
            ("rect", "#8B6239", (0.495, 0.35, 0.015, 0.34)),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One retrieval case.

    Attributes:
        key: Identifier.
        text_en: Scene text in English, written as narration rather than as a
            search query, since that is what the pipeline receives.
        text_fr: The same content in French. A translation of the *meaning*,
            not word-for-word, so it reads naturally.
        correct: Key of the image that should rank first.
        acceptable: Other images that would also be reasonable. Counted as hits
            for top-3 but not for top-1, so a near-miss is not scored as a
            failure when a human would accept it.
    """

    key: str
    text_en: str
    text_fr: str
    correct: str
    acceptable: tuple[str, ...] = field(default=())


EVAL_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        key="sun",
        text_en="The morning sun rose bright and yellow over the horizon",
        text_fr="Le soleil du matin se leva, brillant et jaune, sur l'horizon",
        correct="sun_sky",
    ),
    EvalCase(
        key="moon",
        text_en="At night the moon appeared among the stars",
        text_fr="La nuit, la lune apparut parmi les étoiles",
        correct="night_moon",
    ),
    EvalCase(
        key="mountains",
        text_en="Snow covered mountain peaks rose above the valley",
        text_fr="Les sommets enneigés des montagnes dominaient la vallée",
        correct="mountains_snow",
        acceptable=("snow_winter",),
    ),
    EvalCase(
        key="ocean",
        text_en="Waves rolled across the deep blue ocean",
        text_fr="Les vagues déferlaient sur l'océan d'un bleu profond",
        correct="ocean_waves",
    ),
    EvalCase(
        key="forest",
        text_en="The forest was thick with tall green pine trees",
        text_fr="La forêt était dense, pleine de grands pins verts",
        correct="forest_trees",
    ),
    EvalCase(
        key="desert",
        text_en="Sand dunes stretched across the burning desert",
        text_fr="Les dunes de sable s'étendaient à travers le désert brûlant",
        correct="desert_sand",
    ),
    EvalCase(
        key="fire",
        text_en="Flames burned orange and red in the darkness",
        text_fr="Les flammes brûlaient, orange et rouges, dans l'obscurité",
        correct="fire_flames",
    ),
    EvalCase(
        key="winter",
        text_en="Snow fell softly all through the winter afternoon",
        text_fr="La neige tombait doucement tout l'après-midi d'hiver",
        correct="snow_winter",
        acceptable=("mountains_snow",),
    ),
    EvalCase(
        key="city",
        text_en="Tall buildings towered over the busy city streets",
        text_fr="De grands immeubles dominaient les rues animées de la ville",
        correct="city_buildings",
    ),
    EvalCase(
        key="flower",
        text_en="A single red flower bloomed in the garden",
        text_fr="Une seule fleur rouge s'épanouissait dans le jardin",
        correct="flower_red",
    ),
    EvalCase(
        key="road",
        text_en="The road stretched far into the distance",
        text_fr="La route s'étirait loin dans le lointain",
        correct="road_highway",
    ),
    EvalCase(
        key="book",
        text_en="She opened the book and began to read",
        text_fr="Elle ouvrit le livre et commença à lire",
        correct="book_reading",
    ),
)
