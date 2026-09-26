from app.db import init_db
from app.sync import _set_job, job_status, reset_job


def setup_function():
    init_db()


def test_job_status_hides_percent_when_idle():
    reset_job("Idle")
    data = job_status()
    assert data["status"] == "idle"
    assert data["percent"] is None


def test_job_status_percent_only_while_running_with_total():
    _set_job(status="running", message="Loading…", step="radarr", current=25, total=100)
    data = job_status()
    assert data["percent"] == 25

    _set_job(status="running", message="Fetching…", step="tautulli", current=12, total=0)
    data = job_status()
    assert data["percent"] is None

    _set_job(status="idle", message="1,000 titles", step="", current=0, total=0, finished_at=1)
    assert job_status()["percent"] is None
