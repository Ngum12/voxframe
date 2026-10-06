"""Camera choices are bounded, versioned, undoable and included in cache keys."""
import pytest

from tests.unit import test_api_editing as fixtures
from tests.unit.test_segment_cache import _key, _scene
from voxframe.config.camera import CameraMove
from voxframe.plan.camera_studio import configure
from voxframe.plan.editing import EditError
from voxframe.render.motion.ken_burns import MotionDirection, plan_move

context = fixtures.context
client = fixtures.client
library = fixtures.library
finished_job = fixtures.finished_job


@pytest.mark.parametrize("direction", ["in", "out", "left", "right", "up", "down"])
@pytest.mark.parametrize("strength", [0, .5, 1])
def test_explicit_moves_stay_inside_crop(direction, strength):
    move = plan_move(0, "photo", 90, direction=MotionDirection(direction),
                     intensity=strength, subject_center=(.01, .99))
    for zoom, center in ((move.start_zoom, move.start_center), (move.end_zoom, move.end_center)):
        assert 1 <= zoom <= 1.15
        assert all(.5 / zoom - .0001 <= c <= 1 - .5 / zoom + .0001 for c in center)
    if strength == 0:
        assert move.start_zoom == move.end_zoom == 1
        assert move.start_center == move.end_center == (.5, .5)
    elif direction in ("left", "right", "up", "down"):
        assert move.pan_distance > 0
        assert move.start_zoom == move.end_zoom > 1


def test_camera_choice_invalidates_segments_and_survives_new_plan(library):
    plan = fixtures._plan(library)
    updated = configure(plan, 1, True, CameraMove(direction="left", strength=.75))
    assert plan.scenes[1].camera_move is None
    assert updated.scenes[1].camera_move.direction == "left"
    assert _key(_scene()) != _key(_scene(camera_move=CameraMove(direction="left")))
    assert _key(_scene(camera_move=CameraMove(direction="left"))) != _key(
        _scene(camera_move=CameraMove(direction="right")))
    assert _key(_scene(camera_move=CameraMove(strength=.5))) != _key(
        _scene(camera_move=CameraMove(strength=1)))
    with pytest.raises(EditError):
        configure(plan, 0, True, None)
    with pytest.raises(EditError):
        configure(plan, 2, True, None)


def test_save_stale_guard_undo_redo_and_auto(client, context, finished_job):
    url = f"/api/jobs/{finished_job}/scenes/1/camera"
    controls = client.get(url)
    assert controls.status_code == 200
    choice = {**controls.json(), "settings": {"direction": "right", "strength": .8}}
    response = client.put(url, json=choice)
    assert response.status_code == 200, response.text
    assert response.json()["scene"]["camera_move"] == choice["settings"]
    assert client.put(url, json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert fixtures._saved_plan(context, finished_job).scenes[1].camera_move is None
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert fixtures._saved_plan(context, finished_job).scenes[1].camera_move.direction == "right"
    fresh = client.get(url).json()
    assert client.put(url, json={**fresh, "settings": None}).status_code == 200
    assert fixtures._saved_plan(context, finished_job).scenes[1].camera_move is None
    assert client.put(url, json={**fresh, "settings": {"direction": "right", "strength": 2}}).status_code == 422


def test_preview_does_not_edit_saved_plan(client, context, finished_job, monkeypatch):
    path = context.store.artifact_path(finished_job, "plan")
    before = path.read_bytes()
    url = f"/api/jobs/{finished_job}/scenes/1/camera"
    controls = client.get(url).json()
    def preview(plan, index, directory):
        assert plan.scenes[index].camera_move.direction == "down"
        directory.mkdir(parents=True)
        result = directory / f'{"a" * 24}.mp4'
        result.write_bytes(b"preview")
        return result, 3.0
    monkeypatch.setattr("voxframe.render.motion.preview.camera_preview", preview)
    response = client.post(url + "/preview", json={**controls, "settings": {"direction": "down"}})
    assert response.status_code == 200
    assert path.read_bytes() == before
    assert client.get(response.json()["url"]).content == b"preview"
    assert client.get(f"/api/jobs/{finished_job}/camera-previews/invalid").status_code == 404
    assert client.get(f"/api/jobs/{finished_job}/scenes/0/camera").status_code == 422
