"""Complete drafts include music and choose an immutable preview, never new controls."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from voxframe.api.app import ApiContext
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.complete_audition import CompleteChoice, audition
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision
from voxframe.render.compose.complete_preview import complete_preview, winner

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


@pytest.fixture
def draft(tmp_path: Path) -> ScenePlan:
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    track = tmp_path / "track.wav"
    track.write_bytes(b"track")
    return source().model_copy(update={"audio_path": str(voice), "music_path": str(track),
                                       "music_credit": "Artist - CC0"})


@pytest.fixture
def render(monkeypatch: pytest.MonkeyPatch) -> list:
    captured = []

    def fake(plan, _audio, _style, _caps, output, **kwargs):  # type: ignore[no-untyped-def]
        captured.append((plan, kwargs))
        output.write_bytes(b"a complete video")
        return SimpleNamespace(sound={"passed": True, "problems": []}, music_note="Music fitted")

    monkeypatch.setattr("voxframe.render.compose.complete_preview.render_from_plan", fake)
    monkeypatch.setattr("voxframe.render.compose.complete_preview.probe_capabilities", lambda: None)
    monkeypatch.setattr("voxframe.render.compose.complete_preview.Stems.load",
                        lambda _path: SimpleNamespace(music=Path("music.wav")))
    return captured


@pytest.mark.parametrize("look", [None, "authority", "energy", "cinema"])
def test_whole_current_story_and_selected_mix_are_preserved(draft: ScenePlan, look: str) -> None:
    choice = CompleteChoice(revision=revision(draft), look=look, match_captions=bool(look),
                           mix=AudioMix(music_db=-6, music_arc="rise"))
    before = draft.model_dump_json()
    result = audition(draft, choice)
    assert result.total_frames == draft.total_frames and result.aspect == draft.aspect
    assert result.music_path == draft.music_path and result.music_credit == draft.music_credit
    assert result.audio_mix == choice.mix
    assert draft.model_dump_json() == before


def test_none_and_library_are_explicit_and_invalid_combinations_refused(draft: ScenePlan) -> None:
    choice = CompleteChoice(revision=revision(draft), mix=draft.audio_mix, music_source="none")
    result = audition(draft, choice)
    assert not result.music_path and not result.music_credit and result.score is None
    library_choice = choice.model_copy(update={"music_source": "library",
                                               "music_library_id": "a" * 64})
    selected = audition(draft, library_choice, track="new.wav", credit="New artist")
    assert selected.music_path == "new.wav" and selected.music_credit == "New artist"
    with pytest.raises(EditError):
        audition(draft, library_choice)
    with pytest.raises(EditError):
        audition(draft, choice.model_copy(update={"music_library_id": "a" * 64}))


def test_preview_passes_real_music_settings_and_caches_exact_plan(draft: ScenePlan, render: list,
                                                               tmp_path: Path) -> None:
    folder = tmp_path / "complete"
    result = complete_preview(draft, revision(draft), folder)
    assert render[0][1]["music"].path == Path(draft.music_path)
    assert render[0][1]["music"].credit == draft.music_credit
    assert render[0][0] == draft
    assert result["has_music"] and "added music" in result["note"]
    assert winner(folder, result["key"], revision(draft)) == draft
    assert complete_preview(draft, revision(draft), folder) == result
    assert len(render) == 1
    changed = draft.model_copy(update={"audio_mix": AudioMix(music_arc="punch")})
    assert complete_preview(changed, revision(draft), folder)["key"] != result["key"]


def test_changed_sources_and_wrong_revisions_cannot_choose_old_preview(
    draft: ScenePlan, render: list, tmp_path: Path,
) -> None:
    folder = tmp_path / "complete"
    result = complete_preview(draft, revision(draft), folder)
    with pytest.raises(ValueError):
        winner(folder, result["key"], "a" * 24)
    Path(draft.music_path).write_bytes(b"a changed soundtrack")
    with pytest.raises(ValueError, match="source file changed"):
        winner(folder, result["key"], revision(draft))
    assert complete_preview(draft, revision(draft), folder)["key"] != result["key"]


def test_music_failure_cannot_be_presented_as_a_complete_music_audition(
    draft: ScenePlan, render: list, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("voxframe.render.compose.complete_preview.Stems.load",
                        lambda _path: SimpleNamespace(music=None))
    folder = tmp_path / "complete"
    with pytest.raises(ValueError, match="selected music could not be rendered"):
        complete_preview(draft, revision(draft), folder)
    assert not list(folder.glob("*.json")) and not list(folder.glob("*.mp4"))
    silent = draft.model_copy(update={"music_path": "", "music_credit": ""})
    result = complete_preview(silent, revision(draft), folder)
    assert not result["has_music"] and "No added music" in result["note"]


def test_renderer_changes_expire_the_old_winner(draft: ScenePlan, render: list,
                                               tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "complete"
    snapshot = complete_preview(draft, revision(draft), folder)
    monkeypatch.setattr("voxframe.render.compose.complete_preview.RENDERER_VERSION", 999)
    with pytest.raises(ValueError, match="Render this audition again"):
        winner(folder, snapshot["key"], revision(draft))


def test_source_changes_during_render_cannot_commit_a_misleading_snapshot(
    draft: ScenePlan, render: list, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def changed(_plan, _audio, _style, _caps, output, **kwargs):  # type: ignore[no-untyped-def]
        output.write_bytes(b"video")
        Path(draft.music_path).write_bytes(b"replacement during rendering")
        return SimpleNamespace(sound={}, music_note="")

    monkeypatch.setattr("voxframe.render.compose.complete_preview.render_from_plan", changed)
    folder = tmp_path / "complete"
    with pytest.raises(ValueError, match="changed while rendering"):
        complete_preview(draft, revision(draft), folder)
    assert not list(folder.glob("*.json")) and not list(folder.glob("*.mp4"))


def test_api_preview_never_edits_and_winner_is_one_undoable_snapshot(
    client: TestClient, context: ApiContext, finished_job: str, render: list,
) -> None:
    folder = context.store.job_directory(finished_job)
    audio, track = folder / "voice.wav", folder / "track.wav"
    audio.write_bytes(b"voice")
    track.write_bytes(b"track")
    original = source().model_copy(update={"audio_path": str(audio), "music_path": str(track),
                                            "music_credit": "Original credit"})
    path = context.store.artifact_path(finished_job, "plan")
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/complete-auditions"
    data = client.get(endpoint).json()
    choice = {"revision": data["revision"], "look": "energy", "match_captions": True,
              "music_source": "project", "mix": AudioMix(music_arc="punch").model_dump()}
    response = client.post(endpoint + "/preview", json=choice)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert "plan" not in preview and "stamps" not in preview
    assert preview["has_music"]
    assert ScenePlan.load(path) == original
    assert client.get(preview["url"], headers={"range": "bytes=0-2"}).status_code == 206
    winner_choice = {"revision": data["revision"], "preview_id": preview["preview_id"]}
    result = client.put(endpoint, json=winner_choice)
    assert result.status_code == 200 and result.json()["pending_edits"] == 1
    assert ScenePlan.load(path) == render[0][0]
    assert client.put(endpoint, json=winner_choice).status_code == 409
    assert client.post(endpoint + "/preview", json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == render[0][0]
    client.headers.pop("x-voxframe-token")
    assert client.get(preview["url"]).status_code == 401


def test_unrendered_winner_cannot_change_plan(client: TestClient, context: ApiContext,
                                           finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = ScenePlan.load(path)
    endpoint = f"/api/jobs/{finished_job}/complete-auditions"
    data = client.get(endpoint).json()
    result = client.put(endpoint, json={"revision": data["revision"], "preview_id": "a" * 24})
    assert result.status_code == 409
    assert ScenePlan.load(path) == original


def test_library_credit_is_frozen_in_the_preview_even_if_library_metadata_changes(
    client: TestClient, context: ApiContext, finished_job: str, render: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voxframe.music.library import MusicLibrary

    folder = context.store.job_directory(finished_job)
    audio = folder / "voice.wav"
    audio.write_bytes(b"voice")
    original = source().model_copy(update={"audio_path": str(audio)})
    path = context.store.artifact_path(finished_job, "plan")
    original.save(path)
    track_file = folder / "library-track.wav"
    track_file.write_bytes(b"saved track")
    monkeypatch.setattr(MusicLibrary, "inspect_audio", staticmethod(lambda p: (7, p.stat().st_size)))
    saved = MusicLibrary(context.settings.library_path / "music")
    track, _ = saved.import_track(track_file, "Original title", "Original credit", "calm")
    endpoint = f"/api/jobs/{finished_job}/complete-auditions"
    data = client.get(endpoint).json()
    choice = {"revision": data["revision"], "mix": original.audio_mix.model_dump(),
        "music_source": "library", "music_library_id": track.id}
    response = client.post(endpoint + "/preview", json=choice)
    assert response.status_code == 200, response.text
    preview = response.json()
    saved.update(track.id, "New title", "New credit", "calm")
    result = client.put(endpoint, json={"revision": data["revision"],
                                      "preview_id": preview["preview_id"]})
    assert result.status_code == 200, result.text
    chosen = ScenePlan.load(path)
    assert chosen.music_path == str(saved.file(track))
    assert chosen.music_credit == "Original credit"
    assert chosen == render[0][0]


def test_complete_preview_refuses_source_media_outside_allowed_paths(
    client: TestClient, context: ApiContext, finished_job: str,
) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = source().model_copy(update={"audio_path": "/outside/private.wav"})
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/complete-auditions"
    data = client.get(endpoint).json()
    result = client.post(endpoint + "/preview", json={"revision": data["revision"],
        "mix": original.audio_mix.model_dump(), "music_source": "none"})
    assert result.status_code == 403
    assert "/outside" not in result.text
    assert ScenePlan.load(path) == original
