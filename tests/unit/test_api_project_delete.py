"""Project deletion uses the authenticated API and refuses busy jobs."""
# ruff: noqa: F811
from tests.unit.test_api_routes import _client, context  # noqa: F401


def test_delete_requires_authentication_and_an_existing_stopped_job(context):
    job = context.store.create(audio_name="talk.mp4", options={})
    assert _client(context, with_token=False).delete(f"/api/jobs/{job.id}").status_code == 401
    client = _client(context)
    assert client.delete(f"/api/jobs/{job.id}").status_code == 409
    context.store.submit(job, lambda _: None)
    job.future.result(timeout=10)
    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == 200
    assert response.json() == {"removed": True, "files_deleted": True}
    assert client.get(f"/api/jobs/{job.id}").status_code == 404
    assert client.delete(f"/api/jobs/{job.id}").status_code == 404
    assert client.get("/api/jobs").json() == {"jobs": []}
    context.store.shutdown()
