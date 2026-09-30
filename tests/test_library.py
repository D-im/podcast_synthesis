from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.ports import OnePager, Section
from app.store import artifacts, db, episodes
from app.web.app import create_app


def vid(n):
    return f"vid{n:08d}"[:11]


@pytest.fixture
def env(tmp_path):
    db.bootstrap(tmp_path)
    return TestClient(create_app([], tmp_path)), tmp_path


def add(d, n, title=None, job="queued", failed_step=None, pages=(), age=None):
    """Fake Episode n (higher n = newer) with optional One-Pager versions (list of texts)."""
    v = vid(n)
    created = (datetime(2026, 1, 1, 12, tzinfo=timezone.utc) + timedelta(minutes=n)).isoformat()
    with closing(db.connect(d)) as c:
        c.execute("INSERT INTO episodes (video_id,url,title,created_at) VALUES (?,?,?,?)",
                  (v, f"https://www.youtube.com/watch?v={v}", title, created))
        jid = c.execute("INSERT INTO jobs (video_id,state,created_at,updated_at) "
                        "VALUES (?,?,?,?)", (v, job, created, created)).lastrowid
        for i, name in enumerate(("download", "transcribe", "summarize", "verify"), 1):
            st = "failed" if name == failed_step else "pending"
            c.execute("INSERT INTO steps (job_id,name,ordinal,state,message) VALUES (?,?,?,?,?)",
                      (jid, name, i, st, "boom" if st == "failed" else None))
        for ver, text in enumerate(pages, 1):
            if text is not None:
                artifacts.write_json(artifacts.one_pager_path(d, v, ver),
                                     OnePager((Section("Summary", text),)).to_dict())
            episodes.add_one_pager_version(c, v, ver, "m", {})
        c.commit()
    return v


def test_empty(env):
    client, _ = env
    assert "No Episodes yet." in client.get("/").text


def test_list_newest_first_with_states(env):
    client, d = env
    add(d, 1, "Old one", job="done")
    add(d, 2, None, job="running")
    add(d, 3, "Bad one", job="failed", failed_step="transcribe")
    t = client.get("/").text
    assert t.index("Bad one") < t.index(vid(2)) < t.index("Old one")
    assert "Failed at transcribe" in t and "Done" in t and "Downloading" in t
    assert f'href="/episodes/{vid(1)}"' in t and "2026-01-01" in t
    assert "Verdict" not in t


def test_default_limit_100(env):
    client, d = env
    for n in range(1, 106):
        add(d, n, f"T{n:03d}x")
    t = client.get("/").text
    assert t.count("<li>") == 100 and "T105x" in t and "T006x" in t and "T005x" not in t


def test_title_search_case_insensitive(env):
    client, d = env
    add(d, 1, "Peter Thiel talk")
    add(d, 2, "Other")
    t = client.get("/?q=THIEL").text
    assert "Peter Thiel talk" in t and ">Other<" not in t


def test_one_pager_search_snippet_and_multiword(env):
    client, d = env
    add(d, 1, "A", pages=["Sleep matters and caffeine hurts"])
    add(d, 2, "B", pages=["Only sleep here"])
    t = client.get("/?q=sleep caffeine").text
    assert ">A<" in t and ">B<" not in t and "<small>" in t and "Sleep matters" in t
    assert "caffeine" in client.get("/?q=caffeine").text


def test_only_latest_version_searched(env):
    client, d = env
    add(d, 1, "A", pages=["oldword here", "newword here"])
    assert "No Episodes match 'oldword'." in client.get("/?q=oldword").text
    assert ">A<" in client.get("/?q=newword").text


def test_no_match_escaped(env):
    client, d = env
    add(d, 1, "A")
    assert "No Episodes match 'zzz'." in client.get("/?q=zzz").text


@pytest.mark.parametrize("q", ["", "%20%20", "%20"])
def test_blank_shows_list(env, q):
    client, d = env
    add(d, 1, "Alpha")
    t = client.get(f"/?q={q}").text
    assert "Alpha" in t and "No Episodes match" not in t


@pytest.mark.parametrize("q", ["%25", "_", "<script>alert(1)</script>", "'", '"'])
def test_hostile_query(env, q):
    client, d = env
    add(d, 1, "50% off_deal", pages=["x"])
    r = client.get("/", params={"q": q} if q != "%25" else {"q": "%"})
    assert r.status_code == 200 and "<script>alert" not in r.text
    if q.startswith("<"):
        assert "&lt;script&gt;" in r.text


def test_percent_and_underscore_literal(env):
    client, d = env
    add(d, 1, "plain")
    add(d, 2, "50% off_deal")
    assert ">plain<" not in client.get("/", params={"q": "%"}).text
    assert ">plain<" not in client.get("/", params={"q": "_"}).text
    assert "50% off_deal" in client.get("/", params={"q": "%"}).text


def test_long_query_cut(env):
    client, d = env
    add(d, 1, "A")
    r = client.get("/", params={"q": "a" * 5000})
    assert r.status_code == 200 and "a" * 200 in r.text and "a" * 201 not in r.text


def test_unreadable_one_pager(env):
    client, d = env
    v = add(d, 1, "Title word", pages=["hello"])
    artifacts.one_pager_path(d, v, 1).write_text("{not json")
    assert client.get("/").status_code == 200
    assert "Title word" in client.get("/?q=title").text
    assert "No Episodes match 'hello'." in client.get("/?q=hello").text
    artifacts.one_pager_path(d, v, 1).unlink()  # missing file
    assert client.get("/?q=hello").status_code == 200


def test_cutoff_note(env):
    client, d = env
    for n in range(1, 204):
        add(d, n, f"match{n}")
    t = client.get("/?q=match").text
    assert t.count("<li>") == 200 and "first 200 matches" in t
    assert "first 200" not in client.get("/?q=match1").text


def test_error_page_shows_library(env):
    client, d = env
    add(d, 1, "Listed")
    r = client.post("/submit", data={"url": "nope"})
    assert r.status_code == 400 and "Listed" in r.text and "valid YouTube" in r.text


def test_episode_page_audio(env):
    client, d = env
    v = add(d, 1, "Ep", job="done")
    t = client.get(f"/episodes/{v}").text
    assert "<audio" not in t and "2026-01-01" in t
    assert f'href="https://www.youtube.com/watch?v={v}"' in t
    assert client.get(f"/episodes/{v}/audio").status_code == 404
    path = artifacts.artifact_path(d, v, artifacts.AUDIO)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"abc")
    t = client.get(f"/episodes/{v}").text
    assert '<audio controls preload="none"' in t and f"/episodes/{v}/audio" in t
    r = client.get(f"/episodes/{v}/audio")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mp4")
    assert r.content == b"abc"


def test_audio_bad_id(env):
    client, _ = env
    assert client.get("/episodes/bad.id/audio").status_code == 404
    assert client.get("/episodes/%2e%2e/audio").status_code == 404


# --- review patches ---

def test_snippet_survives_characters_whose_case_folding_changes_length(env):
    client, d = env
    add(d, 1, "Plain", pages=["Die Straße war lang. Der İstanbul Bericht erwähnt die Zukunft."])
    t = client.get("/", params={"q": "zukunft"}).text
    assert "Plain" in t and "<small>" in t and "Zukunft" in t
    t = client.get("/", params={"q": "bericht"}).text
    assert "<small>" in t and "Bericht" in t


def test_library_degrades_when_the_database_cannot_be_read(env, monkeypatch):
    import sqlite3
    from app.web import app as web_app
    client, d = env
    add(d, 1, "Listed")

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(web_app, "build_library", boom)
    r = client.get("/")
    assert r.status_code == 200 and "The library is unavailable right now." in r.text
    assert "No Episodes yet." not in r.text and 'name="url"' in r.text
    monkeypatch.setattr(web_app, "build_library", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    assert client.get("/").status_code == 200


def test_list_shows_the_newest_job_and_respects_the_limit(env):
    client, d = env
    v = add(d, 1, "Retried", job="failed", failed_step="transcribe")
    add(d, 2, "Other", job="done")
    with closing(db.connect(d)) as c:       # a newer job for the first Episode
        jid = c.execute("INSERT INTO jobs (video_id,state,created_at,updated_at) VALUES "
                        "(?,?,?,?)", (v, "running", "2026-06-01T00:00:00+00:00",
                                      "2026-06-01T00:00:00+00:00")).lastrowid
        for i, name in enumerate(("download", "transcribe", "summarize", "verify"), 1):
            c.execute("INSERT INTO steps (job_id,name,ordinal,state) VALUES (?,?,?,?)",
                      (jid, name, i, "running" if name == "transcribe" else
                       ("done" if name == "download" else "pending")))
        c.execute("INSERT INTO episodes (video_id,url,created_at) VALUES "
                  "('nojob00000a','https://www.youtube.com/watch?v=nojob00000a','2026-01-01T00:00:00+00:00')")
    t = client.get("/").text
    row = t[t.index("Retried"):t.index("Retried") + 300]
    assert "Transcribing" in row and "Failed at" not in row
    assert "nojob00000a" in t                      # an Episode with no job is still listed
    with closing(db.connect(d)) as c:
        assert len(episodes.list_episodes(c, 2)) == 2 and len(episodes.list_episodes(c)) == 3


def test_finished_episode_page_shows_one_pager_spend_transcript_and_audio(env):
    client, d = env
    v = add(d, 1, "Finished", job="done", pages=["The big idea is sleep."])
    with closing(db.connect(d)) as c:
        c.execute("UPDATE steps SET state='done' WHERE job_id=(SELECT id FROM jobs WHERE video_id=?)", (v,))
        c.execute("INSERT INTO spend_ledger (video_id,step,provider,amount_micro_usd,created_at) "
                  "VALUES (?,?,?,?,?)", (v, "transcribe", "fake", 230000, "2026-01-01T12:00:00.000000Z"))
    artifacts.write_json(artifacts.artifact_path(d, v, artifacts.TRANSCRIPT),
                         {"segments": [{"start": 0, "end": 1, "text": "hi"}]})
    audio = artifacts.artifact_path(d, v, artifacts.AUDIO)
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"abcdef")
    t = client.get(f"/episodes/{v}").text
    assert "The big idea is sleep." in t and "$0.2300" in t
    assert f"/episodes/{v}/transcript" in t and "<audio" in t and "Download audio" in t


def test_audio_is_inline_seekable_and_safe(env):
    client, d = env
    v = add(d, 1, "Ep", job="done")
    path = artifacts.artifact_path(d, v, artifacts.AUDIO)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0123456789")
    r = client.get(f"/episodes/{v}/audio")
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers.get("accept-ranges") == "bytes"
    part = client.get(f"/episodes/{v}/audio", headers={"Range": "bytes=2-4"})
    assert part.status_code == 206 and part.content == b"234"
    path.unlink()
    path.mkdir()                                   # a directory where the file should be
    assert client.get(f"/episodes/{v}/audio").status_code == 404


def test_non_http_source_url_is_not_a_link(env):
    client, d = env
    v = add(d, 1, "Ep")
    with closing(db.connect(d)) as c:
        c.execute("UPDATE episodes SET url='javascript:alert(1)' WHERE video_id=?", (v,))
    t = client.get(f"/episodes/{v}").text
    assert 'href="javascript:' not in t and "javascript:alert(1)" in t
