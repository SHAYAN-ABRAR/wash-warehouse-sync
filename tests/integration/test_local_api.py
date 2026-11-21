"""Local production API: security, uploads, preview/review/apply jobs and explorer."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from wash_sync_api.server import create_app

from tests.conftest import aqua_row, aqua_rows, write_survey

TOKEN = "test-token"


@pytest.fixture
def client(engine):
    app = create_app(engine.settings.state_dir, static_dir=None, token=TOKEN)
    with TestClient(app) as c:
        c.headers.update({"X-Wash-Sync-Token": TOKEN, "X-Wash-Sync-User": "api-tester"})
        yield c


def wait(client, job):
    for _ in range(600):
        data = client.get(f"/api/v1/jobs/{job['job_id']}").json()
        if data["status"] in ("succeeded", "failed", "cancelled"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health_is_public_but_everything_else_needs_the_token(engine):
    app = create_app(engine.settings.state_dir, static_dir=None, token=TOKEN)
    with TestClient(app) as c:
        assert c.get("/api/v1/health").json()["mode"] == "local"
        assert c.get("/api/v1/status").status_code == 401
        assert c.get("/api/v1/status", headers={"X-Wash-Sync-Token": "wrong"}).status_code == 401
        assert c.get("/api/v1/status", headers={"X-Wash-Sync-Token": TOKEN}).status_code == 200


def test_untrusted_host_rejected(engine):
    app = create_app(engine.settings.state_dir, static_dir=None, token=TOKEN)
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/api/v1/health").status_code == 400


def test_no_cors_headers(client):
    r = client.options("/api/v1/status", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_full_workflow_over_http(client, world, tmp_path):
    status = client.get("/api/v1/status").json()
    assert status["initialized"] and status["current_version"]["version"] == 0
    w = aqua_rows(world)
    changed = next(r for r in w if "aqua_2026_wet_arsenic_raw" in r)
    src = write_survey(tmp_path / "s.xlsx", [aqua_row(1, "2027 (Dry Season)", w[0]),
                                             aqua_row(2, "2026 (Wet Season)", changed, **{"Arsenic (As)": "0.888"})])
    with open(src, "rb") as fh:
        up = client.post("/api/v1/uploads", files={"file": ("aqua.xlsx", fh)}).json()
    assert up["detection"]["source"] == "AQUAWATCH"
    job = wait(client, client.post("/api/v1/previews", json={"upload_ids": [up["upload_id"]]}).json())
    assert job["status"] == "succeeded", job
    plan_id = job["result"]["plan_id"]
    plan = client.get(f"/api/v1/plans/{plan_id}").json()
    assert plan["summary"]["by_classification"]["NEW_OBSERVATION"] == 1
    items = client.get(f"/api/v1/plans/{plan_id}/review-items", params={"status": "undecided"}).json()
    assert items["total"] == 1 and items["items"][0]["record"]["source_record_id"] == changed["aqua_gwc_id"]
    item_id = items["items"][0]["item_id"]
    bad = client.post(f"/api/v1/plans/{plan_id}/review-items/{item_id}/decision", json={"choice": "ACCEPT_NEW_VALUES", "reason": ""})
    assert bad.status_code == 422
    ok = client.post(f"/api/v1/plans/{plan_id}/review-items/{item_id}/decision",
                     json={"choice": "KEEP_EXISTING", "reason": "Original value verified in lab sheet"})
    assert ok.status_code == 200
    assert client.post(f"/api/v1/plans/{plan_id}/approve", json={}).json()["status"] == "APPROVED"
    applied = wait(client, client.post(f"/api/v1/plans/{plan_id}/apply", json={}).json())
    assert applied["status"] == "succeeded", applied
    assert applied["result"]["new_version"] == 1
    history = client.get("/api/v1/versions").json()
    assert history["current_version"] == 1
    decision_event = next(e for e in history["events"] if e["event"] == "DECISION")
    assert decision_event["by"] == "api-tester" and decision_event["reason"]
    audit = client.get(f"/api/v1/plans/{plan_id}/audit/apply/record_changes.csv")
    assert audit.status_code == 200 and "KEEP_EXISTING" in audit.text
    assert client.get(f"/api/v1/plans/{plan_id}/audit/apply/..%2F..%2Fmanifest.json").status_code == 404
    rows = client.get("/api/v1/warehouse/rows", params={"q": w[0]["aqua_gwc_id"].lower(),
                                                        "columns": "candidate_id,aqua_2027_dry_water_table_m"}).json()
    assert rows["total"] == 1 and rows["items"][0]["aqua_2027_dry_water_table_m"] == 5.5
    detail = client.get(f"/api/v1/warehouse/rows/{rows['items'][0]['row']}").json()
    assert detail["values"]["aqua_gwc_id"] == w[0]["aqua_gwc_id"]
    quality = client.get("/api/v1/quality").json()
    assert quality["rows"] == len(world.rows) and "gps" in quality["quality"]
    download = client.get("/api/v1/versions/1/download")
    assert download.status_code == 200 and download.content[:2] == b"PK"


def test_upload_rejects_unsupported_types(client):
    r = client.post("/api/v1/uploads", files={"file": ("evil.exe", b"MZ")})
    assert r.status_code == 400


def test_spa_fallback_without_build(engine, tmp_path):
    app = create_app(engine.settings.state_dir, static_dir=tmp_path / "no-build", token=TOKEN)
    with TestClient(app) as client:
        r = client.get("/conflicts")
        assert r.status_code == 200 and "local engine is running" in r.text


def test_download_links_work_with_the_page_cookie_but_mutations_need_the_header(engine, tmp_path):
    static = tmp_path / "web"
    static.mkdir()
    (static / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    app = create_app(engine.settings.state_dir, static_dir=static, token=TOKEN)
    with TestClient(app) as c:
        page = c.get("/")
        assert 'name="wash-sync-mode" content="local"' in page.text
        assert c.cookies.get("wash_sync_session") == TOKEN  # set by serving the app (HttpOnly, SameSite=Strict)
        assert c.get("/api/v1/versions/0/download").status_code == 200  # plain link: cookie only
        r = c.post("/api/v1/plans/x/approve", json={})
        assert r.status_code == 401  # state changes still require the header
