"""Portable signatures come from exact fresh opening previews, never current controls."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.unit import test_opening_audition as opening
from voxframe.config.bookend_signatures import BookendStyle, delete, load, save
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.bookend_audition import BookendChoice
from voxframe.plan.scene_plan import PlanWord, ScenePlan
from voxframe.plan.shorts import revision

client = opening.client
context = opening.context
finished_job = opening.finished_job
library = opening.library
render = opening.render
saved = opening.saved


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "config" / "bookend-signatures.json"


def test_persistent_names_recipe_privacy_and_removal(isolated_config):  # type: ignore[no-untyped-def]
    style = BookendStyle(opening_look="energy", closing_look="cinema", opening_shot="speaker", match_captions=False, opening_words=5, closing_words=3)
    item = save(" My   punch ", style)
    assert item.name == "My punch" and load() == [item]
    payload = json.loads(isolated_config.read_text())[0]
    assert set(payload) == {"id", "name", "opening_look", "closing_look", "opening_shot", "closing_shot", "match_captions", "opening_words", "closing_words"}
    assert not {"quote", "audio_path", "asset_scene", "music_path", "replace_opening", "replace_closing", "revision"}.intersection(payload)
    with pytest.raises(ValueError, match="already used"):
        save("my punch", style)
    with pytest.raises(ValueError, match="name"):
        save(" \t ", style)
    delete(item.id)
    assert load() == [] and style.opening_look == "energy"
    with pytest.raises(KeyError):
        delete(item.id)


@pytest.mark.parametrize("body", ["{broken", "null", "{}", '{"unexpected": "value"}', '[{"id": "bad"}]'])
def test_corrupt_files_are_not_overwritten_by_save_or_remove(isolated_config, body):  # type: ignore[no-untyped-def]
    isolated_config.parent.mkdir()
    isolated_config.write_text(body)
    for action in (load, lambda: save("New", BookendStyle(opening_look="energy", closing_look="cinema", opening_words=4, closing_words=3)),
                   lambda: delete("a" * 32)):
        with pytest.raises((ValueError, TypeError)):
            action()
        assert isolated_config.read_text() == body


def test_limit_and_concurrent_saves_keep_all_signatures_and_no_partial_files(isolated_config):  # type: ignore[no-untyped-def]
    style = BookendStyle(opening_look="authority", closing_look="cinema", opening_words=3, closing_words=3)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: save(f"Opening {number}", style), range(30)))
    assert len(load()) == 30 and len({item.id for item in load()}) == 30
    with pytest.raises(ValueError, match="30"):
        save("One more", style)
    assert not list(isolated_config.parent.glob("*.partial"))


def preview(client, job_id, original, **changes):  # type: ignore[no-untyped-def]
    choice = BookendChoice(revision=revision(original), last_word=3, first_word=8, opening_look="energy", closing_look="cinema", **changes)
    response = client.post(f"/api/jobs/{job_id}/bookend-auditions/preview", json=choice.model_dump())
    assert response.status_code == 200, response.text
    return response.json()


def signature_from_preview(client, job_id, result, name="My entrance"):  # type: ignore[no-untyped-def]
    response = client.post(f"/api/jobs/{job_id}/bookend-signatures", json={"name": name,
        "revision": result["revision"], "bookend_id": result["bookend_id"]})
    assert response.status_code == 201, response.text
    return response.json()


def test_save_reads_approved_recipe_without_editing_the_project(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    before = path.read_bytes()
    result = preview(client, finished_job, original, match_captions=False)
    signature = signature_from_preview(client, finished_job, result)
    assert signature["opening_look"] == "energy" and not signature["match_captions"]
    assert signature["opening_words"] == 4 and signature["closing_words"] == 3
    assert path.read_bytes() == before and len(render) == 1
    assert client.get("/api/bookend-signatures").json()["signatures"] == [signature]
    assert context.store.get(finished_job).summary.get("pending_edits", 0) == 0
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures", json={"name": "MY ENTRANCE",
        "revision": result["revision"], "bookend_id": result["bookend_id"]}).status_code == 422


@pytest.mark.parametrize("change", ["revision", "voice", "music", "missing-preview", "recipe", "traversal"])
def test_expired_or_forged_approval_cannot_save_a_signature(client, context, finished_job, saved, render, change):  # type: ignore[no-untyped-def]
    original, path = saved
    result = preview(client, finished_job, original)
    if change == "revision":
        original.model_copy(update={"audio_mix": AudioMix(music_db=-15)}).save(path)
    elif change in {"voice", "music"}:
        Path(original.audio_path if change == "voice" else original.music_path).write_bytes(b"changed")
    elif change == "missing-preview":
        (context.store.job_directory(finished_job) / "complete-previews" / f"{result['preview_id']}.mp4").unlink()
    else:
        record = context.store.job_directory(finished_job) / "bookend-previews" / f"{result['bookend_id']}.json"
        payload = json.loads(record.read_text())
        if change == "recipe":
            payload["choice"]["closing_look"] = "authority"
        else:
            payload["preview_id"] = "../../private"
        record.write_text(json.dumps(payload))
    response = client.post(f"/api/jobs/{finished_job}/bookend-signatures", json={"name": "Expired",
        "revision": result["revision"], "bookend_id": result["bookend_id"]})
    assert response.status_code == 409 and not load()


def new_project(context, original, words="Voici trois idées pour avancer vite avec votre propre voix maintenant."):  # type: ignore[no-untyped-def]
    job = context.store.create(audio_name="New recording", options={})
    folder = context.store.job_directory(job.id)
    audio, music = folder / "new-voice.wav", folder / "new-music.wav"
    audio.write_bytes(b"new voice")
    music.write_bytes(b"new music")
    timed = tuple(PlanWord(text=text, start=.2 + i * .6, end=.5 + i * .6)
                  for i, text in enumerate(words.split()))
    plan = original.model_copy(update={"audio_path": str(audio), "music_path": str(music),
        "music_credit": "New artist", "audio_mix": AudioMix(music_arc="punch", music_db=-11),
        "scenes": (original.scenes[0].model_copy(update={"text": words, "words": timed}),)})
    path = plan.save(folder / "new.plan.json")
    video = folder / "video.mp4"
    video.write_bytes(b"finished")
    context.store.submit(job, lambda _: None)
    job.future.result(timeout=5)
    context.store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    return job, plan, path


def test_new_recording_uses_own_words_music_and_timings_then_exact_undoable_preview(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, old_path = saved
    signature = signature_from_preview(client, finished_job, preview(client, finished_job, original))
    job, plan, path = new_project(context, original)
    before = path.read_bytes()
    response = client.post(f"/api/jobs/{job.id}/bookend-signatures/preview", json={
        "signature_id": signature["id"], "revision": revision(plan), "last_word": 3, "first_word": 8})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["opening"]["quote"] == "Voici trois idées pour"
    assert result["closing"]["quote"] == "propre voix maintenant."
    assert result["settings"]["closing_look"] == "cinema"
    assert result["signature"]["name"] == "My entrance" and result["settings"]["opening_look"] == "energy"
    draft = render[-1][0]
    assert draft.music_path == plan.music_path and draft.music_credit == "New artist"
    assert draft.audio_mix == plan.audio_mix and path.read_bytes() == before
    assert "Why" not in " ".join(s.visual_beat.text for s in draft.scenes if s.visual_beat)
    assert client.put(f"/api/jobs/{job.id}/complete-auditions", json={
        "revision": revision(plan), "preview_id": result["preview_id"]}).status_code == 200
    assert ScenePlan.load(path) == draft
    assert client.post(f"/api/jobs/{job.id}/plan/undo").status_code == 200
    assert path.read_bytes() == before and ScenePlan.load(old_path) == original


def test_recipe_does_not_reuse_pinned_replacement_permission(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    from voxframe.config.visuals import VisualBeat

    original, path = saved
    pinned = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={
        "visual_beat": VisualBeat(text="Pinned", source="user")}),)})
    pinned.save(path)
    signature = signature_from_preview(client, finished_job,
        preview(client, finished_job, pinned, replace_opening=True, replace_closing=True))
    assert not {"replace_opening", "replace_closing"}.intersection(signature)
    response = client.post(f"/api/jobs/{finished_job}/bookend-signatures/preview", json={
        "signature_id": signature["id"], "revision": revision(pinned), "last_word": 3, "first_word": 8})
    assert response.status_code == 422 and "pinned" in response.text


def test_removing_signature_preserves_projects_and_already_reviewed_snapshots(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    result = preview(client, finished_job, original)
    signature = signature_from_preview(client, finished_job, result)
    before = path.read_bytes()
    assert client.delete(f"/api/bookend-signatures/{signature['id']}").status_code == 204
    assert path.read_bytes() == before and not load()
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures/preview", json={
        "signature_id": signature["id"], "revision": revision(original), "last_word": 3, "first_word": 8}).status_code == 404
    assert client.put(f"/api/jobs/{finished_job}/complete-auditions", json={
        "revision": result["revision"], "preview_id": result["preview_id"]}).status_code == 200


def test_corrupt_store_api_reports_it_and_preserves_file(client, context, finished_job, saved, render, isolated_config):  # type: ignore[no-untyped-def]
    result = preview(client, finished_job, saved[0])
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("broken")
    assert client.get("/api/bookend-signatures").status_code == 422
    assert client.delete("/api/bookend-signatures/" + "a" * 32).status_code == 422
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures", json={"name": "Safe",
        "revision": result["revision"], "bookend_id": result["bookend_id"]}).status_code == 422
    assert isolated_config.read_text() == "broken"


def test_missing_approval_and_all_signature_routes_require_authentication(client, finished_job, saved):  # type: ignore[no-untyped-def]
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures", json={"name": "Missing",
        "revision": revision(saved[0]), "bookend_id": "b" * 24}).status_code == 409
    client.headers.clear()
    assert client.get("/api/bookend-signatures").status_code == 401
    assert client.delete("/api/bookend-signatures/" + "a" * 32).status_code == 401
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures", json={}).status_code == 401
    assert client.post(f"/api/jobs/{finished_job}/bookend-signatures/preview", json={}).status_code == 401


def test_failed_atomic_write_preserves_prior_recipes_and_cleans_temporary_files(isolated_config, monkeypatch):  # type: ignore[no-untyped-def]
    style = BookendStyle(opening_look="energy", closing_look="cinema", opening_words=4, closing_words=3)
    signature = save("Original", style)
    before = isolated_config.read_bytes()
    def fail_replace(self, target):  # type: ignore[no-untyped-def]
        raise OSError("disk unavailable")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        save("New", style)
    assert isolated_config.read_bytes() == before and load() == [signature]
    assert not list(isolated_config.parent.glob("*.partial"))


def test_preview_does_not_accept_forged_style_or_overlapping_words(client, finished_job, saved, render):  # type: ignore[no-untyped-def]
    result = preview(client, finished_job, saved[0])
    signature = signature_from_preview(client, finished_job, result)
    url = f"/api/jobs/{finished_job}/bookend-signatures/preview"
    request = {"signature_id": signature["id"], "revision": revision(saved[0]), "last_word": 3, "first_word": 8}
    assert client.post(url, json={**request, "opening_look": "authority"}).status_code == 422
    assert client.post(url, json={**request, "first_word": 3}).status_code == 422
    assert client.post(url, json={**request, "revision": "f" * 24}).status_code == 409


def test_each_pinned_end_needs_new_replacement_permission(client, finished_job, saved, render):  # type: ignore[no-untyped-def]
    from voxframe.config.visuals import VisualBeat

    original, path = saved
    pinned = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={"visual_beat": VisualBeat(text="Pinned", source="user")}),)})
    pinned.save(path)
    signature = signature_from_preview(client, finished_job, preview(client, finished_job, pinned,
        replace_opening=True, replace_closing=True))
    request = {"signature_id": signature["id"], "revision": revision(pinned), "last_word": 3, "first_word": 8}
    url = f"/api/jobs/{finished_job}/bookend-signatures/preview"
    for key in ("replace_opening", "replace_closing"):
        assert client.post(url, json={**request, key: True}).status_code == 422
    assert client.post(url, json={**request, "replace_opening": True, "replace_closing": True}).status_code == 200
