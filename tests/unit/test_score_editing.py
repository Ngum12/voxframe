"""Changing a generated score after the video is made (D-179).

Group stems that add up to the score, group levels that re-mix them without
composing again, intensity, new variations, changing the music choice, and
style previews. Runs on the synthetic samples (``score_kit``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("scipy")

from tests.unit.score_kit import make_samples, make_speech  # noqa: E402
from voxframe.music.score import (  # noqa: E402
    events_for,
    group_paths,
    make_score,
    score_key,
    style_preview,
    styles,
)
from voxframe.plan.audio_mix import AudioMix, ScoreLevels  # noqa: E402
from voxframe.render.audio.mixdown import (  # noqa: E402
    RATE,
    SpeechSpan,
    Stems,
    _mix_block,
    _music_envelope,
    group_powers,
    sum_groups,
)

DURATION = 30.0


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> dict:  # type: ignore[type-arg]
    root = tmp_path_factory.mktemp("score-edit")
    voice = root / "voice.wav"
    words = make_speech(voice, DURATION)
    samples = make_samples(root / "samples")
    result = make_score(
        words=words,
        voice=voice,
        duration=DURATION,
        style_name="inspiring",
        seed=5,
        output=root / "score.flac",
        work_dir=root / "work",
        samples=samples,
    )
    return {"root": root, "voice": voice, "words": words, "samples": samples, "result": result}


def _read(path: Path) -> np.ndarray:
    return soundfile.read(str(path), dtype="float64", always_2d=True)[0]


class TestGroups:
    def test_the_groups_add_up_to_the_score(self, made: dict) -> None:  # type: ignore[type-arg]
        result = made["result"]

        total = _read(result.path)
        parts = sum(_read(path) for path in result.groups.values())

        assert set(result.groups) == {"piano", "strings", "percussion", "bass", "pads"}
        assert result.groups == group_paths(result.path)
        assert float(np.max(np.abs(total - parts))) < 1e-5  # 24-bit rounding
        assert float(np.max(np.abs(total))) > 0.01

    def test_summing_at_levels_scales_each_group(self, made: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
        groups = tuple(made["result"].groups.items())
        levels = {"piano": -6.0, "strings": 0.0, "percussion": -30.0, "bass": 3.0, "pads": 0.0}

        summed = _read(sum_groups(groups, levels, tmp_path / "levels.wav"))

        expected = sum(_read(p) * 10 ** (levels[g] / 20) for g, p in groups)
        assert float(np.max(np.abs(summed - expected))) < 1e-5


class TestLevelsInTheMix:
    def _stems(self, made: dict) -> Stems:  # type: ignore[type-arg]
        groups = tuple(made["result"].groups.items())
        spans = ((1.0, 5.0), (8.0, 12.0))
        return Stems(
            voice=made["voice"],
            music=made["result"].path,
            spans=tuple(SpeechSpan(a, b, -20.0, -30.0) for a, b in spans),
            landing=20.0,
            video_end=DURATION,
            fps=30.0,
            music_groups=groups,
            music_levels=ScoreLevels().as_db(),
            group_power=group_powers(spans, groups, soundfile),
        )

    def test_designed_levels_use_the_score_as_kept(self, made: dict) -> None:  # type: ignore[type-arg]
        stems = self._stems(made)

        assert stems.for_mix(AudioMix(voice_polish=False)) == stems

    def test_new_levels_mix_the_groups_on_the_fly(self, made: dict) -> None:  # type: ignore[type-arg]
        stems = self._stems(made)
        levels = ScoreLevels(piano=-6, percussion=-30, bass=3)
        mix = AudioMix(voice_polish=False, score_levels=levels)

        chosen = stems.for_mix(mix)
        block = _mix_block(chosen, mix, 0, RATE * 10, _music_envelope(chosen, mix), soundfile)
        voice_only = _mix_block(chosen, mix, 0, RATE * 10, _music_envelope(chosen, mix), soundfile, voice_only=True)

        summed = sum_groups(stems.music_groups, dict(levels.as_db()), made["root"] / "check.wav")
        exact = Stems(**{**stems.__dict__, "music": summed, "music_groups": (), "group_power": ()})
        reference = _mix_block(exact, mix, 0, RATE * 10, _music_envelope(chosen, mix), soundfile)
        assert chosen.play_groups and float(np.max(np.abs(block - reference))) < 1e-4
        assert float(np.max(np.abs(block - voice_only))) > 1e-3  # the music is there

    def test_an_even_change_moves_every_estimate_by_it(self, made: dict) -> None:  # type: ignore[type-arg]
        stems = self._stems(made)
        quieter = ScoreLevels(piano=-6, strings=-6, percussion=-6, bass=-6, pads=-6)

        chosen = stems.for_mix(AudioMix(voice_polish=False, score_levels=quieter))

        for before, after in zip(stems.spans, chosen.spans, strict=True):
            assert after.music_db == pytest.approx(before.music_db - 6.0, abs=0.01)

    def test_the_groups_survive_saving(self, made: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
        stems = self._stems(made)
        stems.save(tmp_path / "stems.json")

        assert Stems.load(tmp_path / "stems.json") == stems


class TestIntensityAndVariation:
    def test_intensity_raises_and_lowers_the_energy(self, made: dict) -> None:  # type: ignore[type-arg]
        style = styles()["cinematic"]

        def mean_level(intensity: float) -> float:
            _, composition, _ = events_for(
                made["words"], made["voice"], DURATION, style, 3, intensity
            )
            return float(np.mean([c.level for c in composition.chords]))

        assert mean_level(-1.0) < mean_level(0.0) < mean_level(1.0)

    def test_each_choice_is_its_own_score(self, made: dict) -> None:  # type: ignore[type-arg]
        style = styles()["calm"]
        words, keys = made["words"], set()
        for seed, intensity in ((1, 0.0), (2, 0.0), (1, 0.5)):
            keys.add(score_key(words, DURATION, style, seed, "voice", intensity))

        assert len(keys) == 3


class TestStylePreviews:
    def test_a_preview_is_made_once_and_kept(self, made: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
        first = style_preview("calm", tmp_path, made["samples"])
        stamp = first.stat().st_mtime_ns
        again = style_preview("calm", tmp_path, made["samples"])

        info = soundfile.info(str(first))
        assert again == first and first.stat().st_mtime_ns == stamp
        assert info.duration == pytest.approx(12.0, abs=0.05)
        assert not list(tmp_path.glob(".work-*"))  # nothing left behind


# --- the API -------------------------------------------------------------------------


class TestChangingTheMusic:
    @pytest.fixture
    def job(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        pytest.importorskip("fastapi")
        from voxframe.api.app import ApiContext
        from voxframe.api.security import SessionToken
        from voxframe.config.settings import Settings
        from voxframe.jobs.store import JobStore
        from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan

        root = tmp_path / "web"
        context = ApiContext(
            settings=Settings(library_path=tmp_path / "l", cache_path=tmp_path / "c", output_path=tmp_path / "o"),
            store=JobStore(root, reap_interval=None),
            token=SessionToken("t"),
            allowed_paths=(root.resolve(),),
        )
        track = root / "uploads" / "m" / "song.mp3"
        track.parent.mkdir(parents=True)
        track.write_bytes(b"audio")
        plan = ScenePlan(
            audio_path="a.wav", audio_sha256="0" * 64, audio_duration=5.0, fps=30.0, total_frames=150,
            scenes=(PlannedScene(index=0, start_frame=0, end_frame=150, words=(PlanWord(text="hi", start=0.0, end=0.5),)),),
            music_path=str(track), music_credit="Song by Someone",
        )  # fmt: skip
        return context, plan, track

    def test_switching_to_a_score_and_back_to_the_track(self, monkeypatch: pytest.MonkeyPatch, job) -> None:  # type: ignore[no-untyped-def]
        from voxframe.api import app as api
        from voxframe.api.app import MusicEdit

        context, plan, track = job
        saved: list = []
        state = {"plan": plan}
        fake_job = type("J", (), {"id": "j", "summary": {}})()
        monkeypatch.setattr(api, "_editable_plan", lambda c, i: (fake_job, Path("p.json"), state["plan"]))

        def save(p, path, label=""):  # type: ignore[no-untyped-def]
            saved.append(p)
            state["plan"] = p
            return type("History", (), {"pending": 1})()

        monkeypatch.setattr(api, "_save_plan", save)
        monkeypatch.setattr(api, "_mix_state", lambda j, m, c: {})
        monkeypatch.setattr(context.store, "note_edit", lambda j: None)
        monkeypatch.setattr(context.store, "remember", lambda j, k, v: j.summary.__setitem__(k, v))
        edit = _route(api, context, "edit_music")

        edit("j", MusicEdit(choice="score", style="calm"), context)
        first = state["plan"].score
        edit("j", MusicEdit(choice="score", style="reflective"), context)
        restyled = state["plan"].score
        edit("j", MusicEdit(choice="score", style="reflective", new_variation=True), context)
        varied = state["plan"].score
        edit("j", MusicEdit(choice="score", style="reflective", seed=first.seed, intensity=0.5), context)
        undone = state["plan"].score
        edit("j", MusicEdit(choice="own"), context)

        assert first.style == "calm" and not state["plan"].score
        assert restyled.seed == first.seed and restyled.style == "reflective"  # a style change keeps the variation
        assert varied.seed != first.seed
        assert undone.seed == first.seed and undone.intensity == 0.5
        assert state["plan"].music_path == str(track) and state["plan"].music_credit == "Song by Someone"

    def test_no_track_to_go_back_to_is_said(self, monkeypatch: pytest.MonkeyPatch, job) -> None:  # type: ignore[no-untyped-def]
        from fastapi import HTTPException

        from voxframe.api import app as api
        from voxframe.api.app import MusicEdit

        context, plan, _ = job
        bare = plan.model_copy(update={"music_path": "", "music_credit": ""})
        fake_job = type("J", (), {"id": "j", "summary": {}})()
        monkeypatch.setattr(api, "_editable_plan", lambda c, i: (fake_job, Path("p.json"), bare))
        edit = _route(api, context, "edit_music")

        with pytest.raises(HTTPException) as caught:
            edit("j", MusicEdit(choice="own"), context)
        assert caught.value.status_code == 409
        with pytest.raises(HTTPException) as caught:
            edit("j", MusicEdit(choice="score", style="polka"), context)
        assert caught.value.status_code == 422


def _route(api, context, name: str):  # type: ignore[no-untyped-def]
    """The route function itself, from a freshly built app."""
    app = api.create_app(context)
    for route in app.routes:
        if getattr(route, "name", "") == name:
            return route.endpoint
    raise AssertionError(f"no route {name}")


class TestRememberedTrack:
    def test_it_survives_a_render(self, tmp_path: Path) -> None:
        from voxframe.jobs.store import JobStore

        store = JobStore(tmp_path, reap_interval=None)
        job = type("J", (), {"summary": {"pending_edits": 2}, "artifacts": {}, "warnings": ()})()
        store._persist = lambda: None  # type: ignore[method-assign]

        store.remember(job, "kept_track", {"path": "song.mp3", "credit": ""})  # type: ignore[arg-type]
        store.record_result(job, artifacts={}, warnings=(), summary={"sound": {}})  # type: ignore[arg-type]

        assert job.summary == {"kept_track": {"path": "song.mp3", "credit": ""}, "sound": {}}

    def test_only_kept_details_are_remembered(self, tmp_path: Path) -> None:
        from voxframe.jobs.store import JobStore

        store = JobStore(tmp_path, reap_interval=None)
        with pytest.raises(ValueError, match="kept_"):
            store.remember(type("J", (), {"summary": {}})(), "sound", 1)  # type: ignore[arg-type]
