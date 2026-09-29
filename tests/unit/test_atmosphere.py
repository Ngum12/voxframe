"""The atmospheric fallback (D-137).

A scene that finds nothing gets a calm image on the whole recording's theme,
labelled so it is never taken for a match. These pin how the theme is chosen,
and the rules on which image may be used.
"""

from __future__ import annotations

from pathlib import Path

from voxframe.library import AssetLibrary
from voxframe.match.atmosphere import ATMOSPHERIC, apply_atmosphere, theme_queries
from voxframe.models import Asset, AssetKind, LicenseInfo
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan

SONNET = [
    "January, from a calendar of sonnets by Helen Hunt Jackson,",
    "read for Librevox .org by Fox in the Stars of Shininghalf .com.",
    "A winter, frozen pulse and heart of fire. What loss is theirs who from thy kingdom turn,",
    "dismayed and think thy snow a sculptured urn, of death? Far sooner in mid -summer attire,",
    "Her roses to forego the strength they learn. In sleeping on thy breast, no fires can burn.",
]

FABLE = [
    "Ceci est un enregistrement Librivox, tous nos enregistrements appartiennent "
    "aux domaines publics.",
    "rendez -vous sur Librivox .org. Enregistré par Esoua, fable de la Fontaine.",
    "Fable 1, la Cigale est la fourmi. La Cigale, ayant chanté tout l'été,",
    "Elle alla crier famine chez la fourmi sa voisine,",
    "Je vous paierai, lui dit-elle, avant l'août, foi d'animal,",
]

#: Henry David Thoreau, Walden (1854): aiming high, over two sentences.
TALK = [
    "In the long run men hit only what they aim at.",
    "Therefore, though they should fail immediately, they had better aim at "
    "something high.",
]


class TestTheTheme:
    def test_speech_about_aims_gets_its_metaphors(self) -> None:
        queries = theme_queries(TALK, "en")

        assert queries
        assert "finish line" in queries[0]

    def test_a_winter_sonnet_gets_its_recurring_image(self) -> None:
        """ "heart of fire" and "no fires can burn": two scenes, one theme."""
        assert theme_queries(SONNET, "en") == ["fire"]

    def test_a_fable_gets_its_character_not_its_credits(self) -> None:
        """The LibriVox preamble says "recording" twice in one sentence;
        counting by scene is what stops that being the fable's theme."""
        queries = theme_queries(FABLE, "fr")

        assert "fourmi" in queries
        assert "enregistrement" not in queries

    def test_names_are_never_themes(self) -> None:
        """A reader called Fox became a photograph of foxes once (D-135)."""
        texts = ["read by Fox in the stars", "Fox again in the stars"]

        assert "fox" not in theme_queries(texts, "en")

    def test_archaic_pronouns_are_never_themes(self) -> None:
        assert "thy" not in theme_queries(SONNET, "en")

    def test_nothing_recurring_means_no_theme(self) -> None:
        """An honest gap beats a guess."""
        assert theme_queries(["a red barn", "a blue boat"], "en") == []


# --- choosing images -------------------------------------------------------------

CALM_FIRE = [1.0] + [0.0] * 511
ALSO_FIRE = [0.9, 0.1] + [0.0] * 510
UNRELATED = [0.0, 0.0, 1.0] + [0.0] * 509


class FakeEmbedder:
    model_id = "fake/model"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [CALM_FIRE for _ in texts]


def _library(tmp_path: Path, vectors: dict[str, list[float]]) -> AssetLibrary:
    library = AssetLibrary(tmp_path / "lib")
    for index, (name, vector) in enumerate(vectors.items()):
        library.add(
            Asset(
                id=name, path=Path(f"{name}.jpg"), kind=AssetKind.IMAGE,
                sha256=str(index).ljust(64, "0"), width=1920, height=1080,
                license=LicenseInfo(name="CC0-1.0", author="Test", source="local"),
            ),
            embedding=vector,
            embed_model="fake/model",
        )
    return library


def _asset(asset_id: str) -> PlanAsset:
    return PlanAsset(
        id=asset_id, path=f"{asset_id}.jpg", width=1920, height=1080,
        license_name="CC0", license_author="A", license_source="Test",
    )


def _plan(*scenes: PlannedScene) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64,
        audio_duration=scenes[-1].end_frame / 30, fps=30.0,
        total_frames=scenes[-1].end_frame, scenes=scenes,
    )


def _plain(index: int, **extra: object) -> PlannedScene:
    return PlannedScene(
        index=index, start_frame=index * 100, end_frame=(index + 1) * 100,
        text="no fires can burn", motion=MotionKind.NONE, **extra,
    )


def _filled(index: int, asset_id: str) -> PlannedScene:
    return PlannedScene(
        index=index, start_frame=index * 100, end_frame=(index + 1) * 100,
        text="heart of fire", asset=_asset(asset_id),
    )


class TestChoosingImages:
    def test_a_plain_scene_gets_an_atmospheric_image(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"hearth": CALM_FIRE})

        plan = apply_atmosphere(
            _plan(_plain(0)), library, FakeEmbedder(), ["fire"], threshold=0.17
        )

        scene = plan.scenes[0]
        assert scene.asset is not None and scene.asset.id == "hearth"
        assert scene.asset_source == ATMOSPHERIC

    def test_it_is_labelled_as_not_a_match(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"hearth": CALM_FIRE})

        scene = apply_atmosphere(
            _plan(_plain(0)), library, FakeEmbedder(), ["fire"], threshold=0.17
        ).scenes[0]

        assert "not a match" in scene.match_reason
        assert scene.match_score == 0.0

    def test_an_image_already_in_the_video_is_never_reused(
        self, tmp_path: Path
    ) -> None:
        """A filler that repeats a real match reads as a mistake (D-087)."""
        library = _library(tmp_path, {"hearth": CALM_FIRE, "candle": ALSO_FIRE})

        plan = apply_atmosphere(
            _plan(_filled(0, "hearth"), _plain(1)),
            library, FakeEmbedder(), ["fire"], threshold=0.17,
        )

        assert plan.scenes[1].asset is not None
        assert plan.scenes[1].asset.id == "candle"

    def test_two_plain_scenes_get_two_different_images(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"hearth": CALM_FIRE, "candle": ALSO_FIRE})

        plan = apply_atmosphere(
            _plan(_plain(0), _plain(1)), library, FakeEmbedder(), ["fire"], threshold=0.17
        )

        ids = [scene.asset.id for scene in plan.scenes if scene.asset]
        assert len(ids) == 2 and len(set(ids)) == 2

    def test_below_the_threshold_the_scene_stays_plain(self, tmp_path: Path) -> None:
        """It must still actually be an image *of* the theme."""
        library = _library(tmp_path, {"boat": UNRELATED})

        plan = apply_atmosphere(
            _plan(_plain(0)), library, FakeEmbedder(), ["fire"], threshold=0.17
        )

        assert plan.scenes[0].asset is None

    def test_an_image_of_printed_text_is_skipped(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"sign": CALM_FIRE})

        plan = apply_atmosphere(
            _plan(_plain(0)), library, FakeEmbedder(), ["fire"], threshold=0.17,
            shows_text=lambda asset_id: asset_id == "sign",
        )

        assert plan.scenes[0].asset is None

    def test_a_scene_a_person_cleared_is_left_alone(self, tmp_path: Path) -> None:
        """A person's choice is theirs (D-128)."""
        library = _library(tmp_path, {"hearth": CALM_FIRE})

        plan = apply_atmosphere(
            _plan(_plain(0, asset_source="user")),
            library, FakeEmbedder(), ["fire"], threshold=0.17,
        )

        assert plan.scenes[0].asset is None

    def test_close_matches_are_kept(self, tmp_path: Path) -> None:
        """The person can still pick a close match over the atmosphere."""
        library = _library(tmp_path, {"hearth": CALM_FIRE})

        plan = apply_atmosphere(
            _plan(_plain(0, near_misses=(_asset("close"),))),
            library, FakeEmbedder(), ["fire"], threshold=0.17,
        )

        assert [a.id for a in plan.scenes[0].near_misses] == ["close"]

    def test_one_click_back_to_plain(self, tmp_path: Path) -> None:
        """The owner's requirement: any atmospheric scene can go plain."""
        from voxframe.plan.editing import remove_image

        library = _library(tmp_path, {"hearth": CALM_FIRE})
        plan = apply_atmosphere(
            _plan(_plain(0)), library, FakeEmbedder(), ["fire"], threshold=0.17
        )

        cleared = remove_image(plan, 0).scenes[0]

        assert cleared.asset is None
        assert cleared.asset_source == "user"
