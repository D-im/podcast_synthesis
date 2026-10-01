import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.core.youtube import parse_video_id
from app.store import db, episodes
from app.web.app import create_app

VID = "dQw4w9WgXcQ"


@pytest.fixture
def env(tmp_path):
    db.bootstrap(tmp_path)
    client = TestClient(create_app([], tmp_path), follow_redirects=False)
    return client, tmp_path


def counts(data_dir):
    c = db.connect(data_dir)
    try:
        return [c.execute(f"select count(*) from {t}").fetchone()[0]
                for t in ("episodes", "jobs", "steps")]
    finally:
        c.close()


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"http://youtube.com/watch?v={VID}&t=30s",
    f"https://m.youtube.com/watch?v={VID}&t=30s",
    f"https://music.youtube.com/watch?list=x&v={VID}",
    f"https://youtu.be/{VID}?si=abc",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}?feature=share",
])
def test_parse_accepts(url):
    assert parse_video_id(url) == VID


@pytest.mark.parametrize("url,expected", [
    (f"youtube.com/watch?v={VID}", VID),
    (f"www.youtube.com/watch?v={VID}&t=5", VID),
    (f"youtu.be/{VID}", VID),
    (f"m.youtube.com/shorts/{VID}", VID),
    ("youtube.com/watch?v=B7yl7fEHeKM&source_ve_path=OTY3MTQ"
     "&embeds_referring_euri=https%3A%2F%2Fmarginalrevolution.com%2F", "B7yl7fEHeKM"),
    ("youtube.com/watch?v=B7yl7fEHeKM&embeds_referring_euri=https://marginalrevolution.com/",
     "B7yl7fEHeKM"),
    (f"  YouTube.com/watch?v={VID}\n", VID),
])
def test_parse_accepts_schemeless(url, expected):
    assert parse_video_id(url) == expected


def test_parse_tracking_params_resolve_to_id():
    url = ("youtube.com/watch?v=B7yl7fEHeKM&source_ve_path=OTY3MTQ"
           "&embeds_referring_euri=https%3A%2F%2Fmarginalrevolution.com%2F")
    assert parse_video_id(url) == "B7yl7fEHeKM"


@pytest.mark.parametrize("url", [
    f"youtube.com.evil.com/watch?v={VID}",
    f"evil.com/youtube.com/watch?v={VID}",
    f"user@youtube.com/watch?v={VID}",
    f"youtube.com@evil.com/watch?v={VID}",
    f"javascript:youtube.com/watch?v={VID}",
    "youtube.com/playlist?list=PLabcdefghijk",
    "hello",
])
def test_parse_rejects_schemeless_lookalikes(url):
    assert parse_video_id(url) is None


@pytest.mark.parametrize("url", [
    "", "plain text", "https://example.com/x",
    f"https://youtube.com.evil.com/watch?v={VID}",
    f"https://evilyoutube.com/watch?v={VID}",
    f"ftp://youtube.com/watch?v={VID}",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/playlist?list=PLabcdefghijk",
    f"https://www.youtube.com/watch?v={VID}x",
    "https://youtu.be/",
    f"https://user@youtube.com.evil.com/watch?v={VID}",
])
def test_parse_rejects(url):
    assert parse_video_id(url) is None


def test_new_video_creates_rows_and_page(env):
    client, d = env
    r = client.post("/submit", data={"url": f"  https://youtu.be/{VID}?si=tracking&t=9  "})
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    assert counts(d) == [1, 1, 4]
    c = db.connect(d)
    job = episodes.get_latest_job_with_steps(c, VID)
    ep = episodes.get_episode(c, VID)
    c.close()
    assert job["state"] == "queued"
    assert [(s["name"], s["ordinal"], s["state"]) for s in job["steps"]] == [
        ("download", 1, "pending"), ("transcribe", 2, "pending"),
        ("summarize", 3, "pending"), ("verify", 4, "pending")]
    assert ep["title"] is None and ep["duration_seconds"] is None
    assert ep["url"] == f"https://www.youtube.com/watch?v={VID}"
    page = client.get(r.headers["location"]).text
    assert "Queued" in page and page.count("pending") == 4


def test_duplicate_other_form(env):
    client, d = env
    client.post("/submit", data={"url": f"https://www.youtube.com/watch?v={VID}"})
    r = client.post("/submit", data={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    assert counts(d) == [1, 1, 4]


@pytest.mark.parametrize("url", [
    "https://example.com/x", f"https://youtube.com.evil.com/watch?v={VID}",
    "ftp://youtube.com/watch?v=" + VID, "nonsense", "",
    "https://www.youtube.com/watch?v=short",
])
def test_invalid_rejected_400_no_rows(env, url):
    client, d = env
    r = client.post("/submit", data={"url": url})
    assert r.status_code == 400 and "valid YouTube" in r.text
    assert counts(d) == [0, 0, 0]


def test_missing_field_rejected(env):
    client, d = env
    assert client.post("/submit", data={}).status_code == 400
    assert counts(d) == [0, 0, 0]


def test_mid_create_failure_rolls_back(env):
    _, d = env
    c = db.connect(d)
    with pytest.raises(sqlite3.IntegrityError):
        episodes.create_episode_with_job(c, VID, "u", ["a", "a"])
    assert not c.in_transaction
    c.close()
    assert counts(d) == [0, 0, 0]


def test_unknown_episode_404(env):
    client, _ = env
    assert client.get("/episodes/doesnotexist").status_code == 404


def test_connection_pragmas(tmp_path):
    db.bootstrap(tmp_path)
    c = db.connect(tmp_path)
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into jobs (video_id,state,created_at,updated_at) values ('x','queued','n','n')")
    c.close()


def test_migrations_idempotent_and_keep_data(tmp_path):
    db.bootstrap(tmp_path)
    c = db.connect(tmp_path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 11
    episodes.create_episode_with_job(c, VID, "u", ["download"])
    c.close()
    db.bootstrap(tmp_path)
    c = db.connect(tmp_path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 11
    assert c.execute("select count(*) from episodes").fetchone()[0] == 1
    c.close()


def test_concurrent_duplicate_submits_create_one_episode(tmp_path):
    db.bootstrap(tmp_path)
    results, errors = [], []
    barrier = threading.Barrier(6)

    def go():
        conn = db.connect(tmp_path)
        try:
            barrier.wait()
            _, created = episodes.create_episode_with_job(
                conn, VID, "u", ("download", "transcribe", "summarize", "verify"))
            results.append(created)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert results.count(True) == 1 and results.count(False) == 5
    assert counts(tmp_path) == [1, 1, 4]


def test_failed_migration_rolls_back_and_keeps_version(tmp_path, monkeypatch):
    from app.store import migrations
    monkeypatch.setattr(migrations, "MIGRATIONS", [["CREATE TABLE a (x)", "THIS IS NOT SQL"]])
    conn = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    with pytest.raises(sqlite3.Error):
        migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("select count(*) from sqlite_master").fetchone()[0] == 0


def test_newer_schema_version_refused(tmp_path):
    from app.store import migrations
    conn = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        migrations.migrate(conn)


def test_submit_reports_503_when_database_unavailable(tmp_path, monkeypatch):
    db.bootstrap(tmp_path)
    client = TestClient(create_app([], tmp_path), follow_redirects=False)

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(episodes, "create_episode_with_job", boom)
    r = client.post("/submit", data={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 503 and "try again" in r.text
    assert counts(tmp_path) == [0, 0, 0]


def test_migration_2_upgrades_populated_v1_database(tmp_path):
    from app.store import migrations
    conn = sqlite3.connect(tmp_path / db.DB_NAME, isolation_level=None)
    for stmt in migrations.MIGRATIONS[0]:
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('v', 'u', 'now')")
    conn.execute("INSERT INTO jobs (id, video_id, state, created_at, updated_at) "
                 "VALUES (1, 'v', 'queued', 'now', 'now')")
    conn.execute("INSERT INTO steps (job_id, name, ordinal, state) VALUES (1, 'download', 1, 'pending')")
    conn.close()
    conn = db.connect(tmp_path)
    migrations.migrate(conn)
    job = episodes.get_latest_job_with_steps(conn, "v")
    conn.close()
    assert job["steps"] == [{"name": "download", "ordinal": 1, "state": "pending",
                             "message": None, "retryable": None, "attempts": 0}]
