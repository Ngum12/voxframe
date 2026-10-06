"""Presets are portable settings, with stale-project and persistence guards."""
import pytest

from tests.unit import test_api_editing as fixtures
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.creative_presets import CreativeSettings, delete, load, save
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.shorts import revision

context = fixtures.context
client = fixtures.client
library = fixtures.library
finished_job = fixtures.finished_job


def test_persistence_names_and_no_project_files(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path))
    settings = CreativeSettings(caption_treatment=CAPTION_PRESETS["electric"],
                                audio_mix=AudioMix(music_arc="rise", voice_db=2))
    preset = save("  My   voice  ", settings)
    assert load() == [preset]
    assert preset.name == "My voice"
    assert set(preset.model_dump()) == {"id", "name", "caption_treatment", "audio_mix"}
    with pytest.raises(ValueError, match="already used"):
        save("my voice", settings)
    delete(preset.id)
    assert load() == []
    # Deleting the saved choice cannot mutate the settings already selected.
    assert settings.audio_mix.music_arc == "rise"
    with pytest.raises(KeyError):
        delete(preset.id)


def test_corrupt_file_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "creative-presets.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        save("new", CreativeSettings())
    assert path.read_text() == "broken"


def test_api_capture_selected_scene_and_mix_without_editing_project(client, context, finished_job, tmp_path, monkeypatch):
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path))
    path = context.store.artifact_path(finished_job, "plan")
    plan = fixtures._saved_plan(context, finished_job)
    plan = plan.model_copy(update={"audio_mix": AudioMix(music_arc="punch"),
        "scenes": tuple(s.model_copy(update={"caption_treatment": CAPTION_PRESETS["electric"]})
                        if s.index == 2 else s for s in plan.scenes)})
    plan.save(path)
    before = path.read_bytes()
    url = f"/api/jobs/{finished_job}/creative-presets"
    body = {"name": "My signature", "revision": revision(plan), "scene": 2}
    response = client.post(url, json=body)
    assert response.status_code == 201, response.text
    preset = response.json()
    assert preset["caption_treatment"]["animation"] == "pop"
    assert preset["audio_mix"]["music_arc"] == "punch"
    assert path.read_bytes() == before
    assert client.get("/api/creative-presets").json()["presets"] == [preset]
    assert client.post(url, json={**body, "revision": "stale"}).status_code == 409
    assert client.post(url, json={**body, "scene": 0}).status_code == 422
    assert client.post(url, json=body).status_code == 422
    assert client.delete(f'/api/creative-presets/{preset["id"]}').status_code == 204
    assert client.get("/api/creative-presets").json() == {"presets": []}


def test_render_request_snapshots_settings_and_rejects_paths(client, context, tmp_path):
    from pydantic import ValidationError

    from voxframe.api.app import RenderRequest, _job_options

    audio = tmp_path / "source.wav"
    audio.write_bytes(b"audio")
    settings = CreativeSettings(caption_treatment=CAPTION_PRESETS["pulse"], audio_mix=AudioMix(music_arc="rise"))
    request = RenderRequest(upload_id="example", use_library=False, creative=settings)
    restored = RenderRequest.model_validate(request.model_dump(mode="json"))
    assert _job_options(restored, audio, context).creative == settings
    with pytest.raises(ValidationError):
        RenderRequest(upload_id="example", creative={"music_path": "someone-else.wav"})
