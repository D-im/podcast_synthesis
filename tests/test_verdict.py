import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import build_fakes
from app.core import submit
from app.store import db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
VID2 = "aaaaaaaaaaa"
FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def client(data):
    return TestClient(create_app([], data), follow_redirects=False)


def done_episode(data, vid=VID):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={vid}")
    assert Worker(data, build_fakes()).run_next()


def add_version(data, vid=VID, n=2, model="m2", hashes=None):
    with closing(db.connect(data)) as c:
        episodes.add_one_pager_version(c, vid, n, model, hashes or {"summarize": "h"})


def post(c, rating="worth_it", reason="Great", version="1", vid=VID):
    from urllib.parse import urlencode
    body = {"version": version}
    if rating is not None:
        body["rating"] = rating
    body["reason"] = reason
    return c.post(f"/episodes/{vid}/verdict", content=urlencode(body), headers=FORM)


def verdicts(data, vid=VID):
    with closing(db.connect(data)) as c:
        return episodes.list_verdicts(c, vid)


def test_record_stores_version_rating_reason_time(data):
    done_episode(data)
    r = post(client(data))
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    [v] = verdicts(data)
    assert (v["version"], v["rating"], v["reason"]) == (1, "worth_it", "Great")
    assert v["created_at"]
    page = client(data).get(f"/episodes/{VID}").text
    assert "Worth it" in page and "Great" in page and "rated v1" in page


def test_form_shown_only_with_one_pager(data):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    assert "Record Verdict" not in client(data).get(f"/episodes/{VID}").text
    r = post(client(data))
    assert r.status_code == 400 and verdicts(data) == []
    assert Worker(data, build_fakes()).run_next()   # runs the queued Job
    assert "Record Verdict" in client(data).get(f"/episodes/{VID}").text


@pytest.mark.parametrize("reason", ["", "   \n\t "])
def test_empty_reason_rejected_and_input_kept(data, reason):
    done_episode(data)
    r = post(client(data), "not_worth_it", reason)
    assert r.status_code == 400 and "Give a reason" in r.text
    assert 'value="not_worth_it" checked' in r.text
    assert verdicts(data) == []


@pytest.mark.parametrize("rating", [None, "", "great"])
def test_bad_rating_rejected(data, rating):
    done_episode(data)
    r = post(client(data), rating, "x")
    assert r.status_code == 400 and "Choose worth it" in r.text and verdicts(data) == []
    assert ">x</textarea>" in r.text   # typed reason kept


def test_too_long_reason_rejected_and_kept(data):
    done_episode(data)
    r = post(client(data), reason="y" * 5001)
    assert r.status_code == 400 and "too long" in r.text and verdicts(data) == []
    assert "y" * 5001 in r.text   # typed reason kept
    assert post(client(data), reason="y" * 5000).status_code == 303


def test_second_verdict_kept_latest_current(data):
    done_episode(data)
    c = client(data)
    post(c, "worth_it", "first take")
    post(c, "not_worth_it", "second take")
    assert [v["reason"] for v in verdicts(data)] == ["second take", "first take"]
    page = c.get(f"/episodes/{VID}").text
    assert "Current Verdict: <strong>Not worth it" in page
    assert "Earlier Verdicts" in page and "first take" in page


def test_verdict_on_old_version_is_marked(data):
    done_episode(data)
    post(client(data))
    add_version(data)
    page = client(data).get(f"/episodes/{VID}").text
    assert "rated v1 (an earlier version; the latest is v2)" in page
    assert "This rates One-Pager v2" in page
    assert "Worth it (v1)" in client(data).get("/").text


def test_stale_form_stores_shown_version(data):
    done_episode(data)
    add_version(data)
    assert post(client(data), version="1").status_code == 303
    assert verdicts(data)[0]["version"] == 1


@pytest.mark.parametrize("version", ["", "abc", "0", "99", "-1"])
def test_bad_version_rejected(data, version):
    done_episode(data)
    r = post(client(data), version=version)
    assert r.status_code == 400 and verdicts(data) == []


def test_unknown_episode_404(data):
    assert post(client(data), vid="zzzzzzzzzzz").status_code == 404


def test_library_shows_latest_verdict_only_when_present(data):
    done_episode(data)
    done_episode(data, VID2)
    c = client(data)
    post(c, "worth_it", "a")
    post(c, "not_worth_it", "b")
    page = c.get("/").text
    assert page.count("Not worth it") == 1 and "Worth it" not in page.replace("Not worth it", "")


def test_join_to_version_score_and_hashes(data):
    done_episode(data)
    with closing(db.connect(data)) as c:
        episodes.add_fidelity_score(c, VID, 1, "m", {"verify": "x"}, 0.9, 0.8)
    post(client(data))
    with closing(db.connect(data)) as c:
        row = c.execute(
            "SELECT v.rating, o.prompt_hashes, f.accuracy FROM verdicts v "
            "JOIN one_pager_versions o ON o.video_id = v.video_id AND o.version = v.version "
            "LEFT JOIN fidelity_scores f ON f.video_id = v.video_id AND f.version = v.version"
        ).fetchone()
    assert row[0] == "worth_it" and row[1] and row[2] == 0.9


def test_foreign_key_blocks_missing_version(data):
    done_episode(data)
    with closing(db.connect(data)) as c:
        assert episodes.add_verdict(c, VID, 9, "worth_it", "x") is None
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO verdicts (video_id, version, rating, reason, created_at) "
                      "VALUES (?, 9, 'worth_it', 'x', 't')", (VID,))


def test_hostile_text_escaped(data):
    done_episode(data)
    post(client(data), reason="<script>alert(1)</script>")
    page = client(data).get(f"/episodes/{VID}").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_form_survives_polling_fragment(data):
    done_episode(data)
    assert "Record Verdict" not in client(data).get(f"/episodes/{VID}/status").text


def test_migration_from_schema_7(tmp_path):
    from app.store import migrations
    p = tmp_path / "x.db"
    conn = sqlite3.connect(p, isolation_level=None)
    for v, stmts in enumerate(migrations.MIGRATIONS[:7], start=1):
        for s in stmts:
            conn.execute(s)
        conn.execute(f"PRAGMA user_version = {v}")
    conn.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('a','u','t')")
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 0


def test_rejected_stale_form_keeps_posted_version(data):
    done_episode(data)
    add_version(data)
    r = post(client(data), "worth_it", "", version="1")
    assert r.status_code == 400
    assert "This rates One-Pager v1." in r.text
    assert 'name="version" value="1"' in r.text
    assert verdicts(data) == []


def test_rejected_unknown_version_falls_back_to_latest(data):
    done_episode(data)
    r = post(client(data), "worth_it", "", version="99")
    assert "This rates One-Pager v1." in r.text


def test_crlf_counts_as_one_character(data):
    done_episode(data)
    reason = "\r\n".join(["a" * 49] * 100)   # 100 lines: 4900 + 99 newlines, 5000 with CRLF counted as one
    assert post(client(data), reason=reason).status_code == 303
    assert "\r" not in verdicts(data)[0]["reason"]
