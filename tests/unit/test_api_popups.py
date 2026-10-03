"""The routes for pop-ups (D-198)."""

from __future__ import annotations

from io import BytesIO

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient
from PIL import Image

from tests.unit.test_api_captions import _job, _saved, client, context
from voxframe.api.app import ApiContext
from voxframe.plan.overlays import OverlayKind

__all__ = ["client", "context"]  # the fixtures, shared


def _add(client: TestClient, job: str, **fields: object):  # type: ignore[no-untyped-def]
    return client.post(f"/api/jobs/{job}/overlays", json={"scene": 0, **fields})


class TestStickers:
    def test_the_set_is_listed_with_its_words(self, client: TestClient) -> None:
        stickers = client.get("/api/stickers").json()
        assert len(stickers) >= 40
        wave = next(s for s in stickers if s["name"] == "water-wave")
        assert "flood" in wave["words"]

    def test_a_sticker_is_served_by_name_only(self, client: TestClient) -> None:
        response = client.get("/api/stickers/fire.png")
        assert response.status_code == 200
        assert Image.open(BytesIO(response.content)).size == (512, 512)
        escaping = client.get("/api/stickers/..%2F..%2Fapp.py")
        assert escaping.headers.get("content-type", "").split(";")[0] != "image/png"
        assert client.get("/api/stickers/not-a-sticker").status_code == 404


class TestEditing:
    def test_a_pop_up_is_added_on_a_word(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = _add(client, job, kind="sticker", sticker="water-wave", word=2)
        assert response.status_code == 200
        body = response.json()
        overlay = _saved(context, job).overlays[0]
        assert overlay.kind is OverlayKind.STICKER and overlay.word == 2
        # "Douala" is said at 1.0 s.
        assert body["times"][overlay.id][0] == pytest.approx(1.0)
        assert body["undo_label"] == "a pop-up"

    def test_its_text_shows_in_the_live_document(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        _add(client, job, kind="text", text="3 TIPS")
        assert "3 TIPS" in client.get(f"/api/jobs/{job}/captions").text

    def test_it_can_be_changed_and_taken_out(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        overlay_id = _add(client, job, kind="text", text="Hello").json()["id"]
        changed = client.put(
            f"/api/jobs/{job}/overlays/{overlay_id}",
            json={"scene": 0, "kind": "text", "text": "Hi", "x": 0.2, "entrance": "bounce"},
        )
        assert changed.status_code == 200
        overlay = _saved(context, job).overlays[0]
        assert (overlay.text, overlay.x, overlay.entrance.value) == ("Hi", 0.2, "bounce")
        assert client.delete(f"/api/jobs/{job}/overlays/{overlay_id}").status_code == 200
        assert _saved(context, job).overlays == ()

    @pytest.mark.parametrize(
        "fields",
        [
            {"kind": "text", "text": "  "},
            {"kind": "counter", "text": "lots"},
            {"kind": "sticker", "sticker": "not-a-sticker"},
            {"kind": "text", "text": "x", "word": 9},
            {"kind": "text", "text": "x", "scene": 1},  # a card
            {"kind": "text", "text": "x", "size": 3},
            {"kind": "text", "text": "x", "colour": "#123456"},
        ],
    )
    def test_what_cannot_be_drawn_is_refused(
        self, client: TestClient, context: ApiContext, fields: dict[str, object]
    ) -> None:
        job = _job(context)
        response = client.post(f"/api/jobs/{job}/overlays", json={"scene": 0, **fields})
        assert response.status_code == 422
        assert _saved(context, job).overlays == ()

    def test_a_picture_of_your_own(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        buffer = BytesIO()
        Image.new("RGB", (300, 200), (10, 200, 10)).save(buffer, "PNG")
        response = client.post(
            f"/api/jobs/{job}/overlays/image",
            data={"scene": "0", "word": "1"},
            files={"file": ("map.png", buffer.getvalue())},
        )
        assert response.status_code == 200
        overlay = _saved(context, job).overlays[0]
        assert overlay.kind is OverlayKind.IMAGE and overlay.word == 1
        served = client.get(f"/api/jobs/{job}/overlays/{overlay.id}/image")
        assert served.status_code == 200
        refused = client.post(
            f"/api/jobs/{job}/overlays/image",
            data={"scene": "0"},
            files={"file": ("map.png", b"not an image")},
        )
        assert refused.status_code == 415

    def test_the_progress_bar(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        assert client.put(f"/api/jobs/{job}/progress-bar", json={"on": True}).status_code == 200
        assert _saved(context, job).progress_bar
        assert "\\fscx0" in client.get(f"/api/jobs/{job}/captions").text

    def test_adding_a_title_keeps_pop_ups_on_their_words(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        _add(client, job, kind="text", text="Hi", word=2)
        client.post(f"/api/jobs/{job}/cards", json={"kind": "title", "text": "Floods"})
        plan = _saved(context, job)
        overlay = plan.overlays[0]
        spoken = plan.scenes[overlay.scene]
        assert not spoken.is_card and spoken.caption_words()[2].text == "Douala"
        start, _ = plan.overlay_times(overlay)
        assert start == pytest.approx(spoken.caption_words()[2].start)


class TestSuggestions:
    def test_a_word_suggests_its_sticker(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        suggestions = client.get(f"/api/jobs/{job}/overlays/suggestions").json()
        assert suggestions == [
            {"scene": 0, "word": 0, "kind": "sticker", "value": "water-wave", "because": "Floods"}
        ]
        # Once the scene has a pop-up, it suggests nothing more.
        _add(client, job, kind="text", text="Hi")
        assert client.get(f"/api/jobs/{job}/overlays/suggestions").json() == []
