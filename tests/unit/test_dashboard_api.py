from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from config import get_settings
from dashboard.plugin_api import router
from db import init_db

app = FastAPI()
app.include_router(router, prefix="/api/plugins/daily-life")


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="test-api-")
    tmp_path = Path(tmp)
    monkeypatch.setenv("HERMES_PROFILE_ID", "dashboard_user")
    monkeypatch.setenv("DAILY_LIFE_DATA_DIR", str(tmp_path))

    settings = get_settings(reload=True)
    init_db(settings.db_path)

    with TestClient(app) as test_client:
        yield test_client

    shutil.rmtree(tmp, ignore_errors=True)
    get_settings(reload=True)


def test_get_status_endpoint(client):
    res = client.get("/api/plugins/daily-life/status?profile=dashboard_user")
    assert res.status_code == 200
    data = res.json()
    assert data["profile"] == "dashboard_user"
    assert "brain" in data
    assert "cycle_completed" in data


def test_get_insights_endpoint(client):
    res = client.get("/api/plugins/daily-life/learning/insights?profile=dashboard_user")
    assert res.status_code == 200
    data = res.json()
    assert "learned_calibrations" in data
    assert "shared_insights" in data


def test_get_opportunities_endpoint(client):
    res = client.get("/api/plugins/daily-life/opportunities?profile=dashboard_user")
    assert res.status_code == 200
    data = res.json()
    assert "opportunities" in data


def test_get_briefing_endpoint(client):
    from db import save_briefing_snapshot
    save_briefing_snapshot("2026-11-08", "dashboard_user", {"summary": "All sources ok"})

    res = client.get("/api/plugins/daily-life/briefing/today?profile=dashboard_user&date_str=2026-11-08")
    assert res.status_code == 200
    assert res.json()["briefing"]["summary"] == "All sources ok"


def test_trigger_cycle_endpoint(client):
    res = client.post("/api/plugins/daily-life/cycle/trigger", json={"force": True, "date_str": "2026-11-08"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("completed", "already_completed", "vetoed")
