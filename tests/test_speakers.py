from contextlib import closing
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.adapters.openai import QuoteIndex
from app.core import speakers, submit
from app.core.regenerate import change_summary
from app.ports import OnePager, Section, Segment, Transcript
from app.store import artifacts, db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
FORM = {"content-type": "application/x-www-form-urlencoded"}
SEGS = (Segment(0, 5, "Welcome to the show today.", "A"),
        Segment(5, 12, "Thanks, I think the future is stagnant and slow.", "B"),
        Segment(12, 15, "Interesting, tell me more.", "A"))


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def client(data):
    return TestClient(create_app([], data), follow_redirects=False)


class Recorder:
    """Fakes whose summarizer and verifier remember the Transcript they were given."""

    def __init__(self):
        self.seen = {}
        self.adapters = build_fakes()
        summ, ver = self.adapters.summarizer, self.adapters.verifier
        real_s, real_v = summ.summarize, ver.verify

        def s(transcript, meter, cache=None):
            self.seen["summarize"] = transcript
            return real_s(transcript, meter, cache)

        def v(transcript, one_pager, meter, cache=None):
            self.seen["verify"] = transcript
            return real_v(transcript, one_pager, meter, cache)
        summ.summarize, ver.verify = s, v


def transcript_path(data):
    return artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT)


def episode_with_speakers(data, segs=SEGS):
    """An Episode whose download and transcribe are done with a speaker-labelled Transcript."""
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
        job = episodes.get_latest_job_with_steps(c, VID)
        for name in ("download", "transcribe"):
            episodes.set_step_state(c, job["id"], name, "done")
    artifacts.artifact_path(data, VID, artifacts.AUDIO).parent.mkdir(parents=True, exist_ok=True)
    artifacts.artifact_path(data, VID, artifacts.AUDIO).write_bytes(b"audio")
    artifacts.write_json(transcript_path(data), Transcript(tuple(segs)).to_dict())


def run(data, rec=None):
    rec = rec or Recorder()
    assert Worker(data, rec.adapters).run_next()
    return rec


def save(c, **names):
    return c.post(f"/episodes/{VID}/speakers", content=urlencode(
        {f"name_{k}": v for k, v in names.items()}), headers=FORM)


def stored(data):
    with closing(db.connect(data)) as c:
        return episodes.get_speaker_names(c, VID)


def jobs_and_spend(data):
    with closing(db.connect(data)) as c:
        return (c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM spend_ledger").fetchone()[0])


# ---- rules -------------------------------------------------------------------------------

def test_labels_in_order_and_apply_leaves_original():
    t = Transcript(SEGS + (Segment(15, 16, "x", None), Segment(16, 17, "y", " ")))
    assert speakers.labels(t) == ["A", "B"]
    named = speakers.apply(t, {"A": "Host"})
    assert [s.speaker for s in named.segments][:3] == ["Host", "B", "Host"]
    assert [s.speaker for s in t.segments][:3] == ["A", "B", "A"]
    assert speakers.apply(t, {}) is t and speakers.apply(t, None) is t


@pytest.mark.parametrize("raw,ok", [
    ({"A": "Host"}, True), ({"A": "  "}, True), ({"A": "x" * 60}, True),
    ({"A": "x" * 61}, False), ({"A": "a\nb"}, False), ({"A": "a\x00b"}, False),
    ({"Z": "Host"}, False),
])
def test_validate(raw, ok):
    result = speakers.validate(raw, ["A", "B"])
    assert isinstance(result, dict) is ok


def test_validate_drops_empty_and_allows_shared_names():
    assert speakers.validate({"A": " ", "B": "Peter"}, ["A", "B"]) == {"B": "Peter"}
    assert speakers.validate({"A": "Peter", "B": "Peter"}, ["A", "B"]) == {"A": "Peter", "B": "Peter"}


# ---- web ---------------------------------------------------------------------------------

def test_form_shown_and_names_saved_edited_cleared(data):
    episode_with_speakers(data)
    c = client(data)
    page = c.get(f"/episodes/{VID}").text
    assert "Speaker A" in page and "Speaker B" in page and 'name="name_A"' in page
    r = save(c, A="Host", B="Peter Thiel")
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    assert stored(data) == {"A": "Host", "B": "Peter Thiel"}
    assert 'value="Peter Thiel"' in c.get(f"/episodes/{VID}").text
    save(c, A="Host", B="Peter")
    assert stored(data) == {"A": "Host", "B": "Peter"}
    save(c, A="", B="Peter")
    assert stored(data) == {"B": "Peter"}


def test_transcript_page_shows_names_and_file_is_untouched(data):
    episode_with_speakers(data)
    before = transcript_path(data).read_bytes()
    c = client(data)
    save(c, A="Host", B="Peter Thiel")
    page = c.get(f"/episodes/{VID}/transcript").text
    assert "Host: Welcome" in page and "Peter Thiel: Thanks" in page and "A: " not in page
    assert transcript_path(data).read_bytes() == before


@pytest.mark.parametrize("names", [{"A": "x" * 61}, {"A": "a\nb"}, {"Z": "Host"}])
def test_invalid_rejected_nothing_stored_and_typed_kept(data, names):
    episode_with_speakers(data)
    r = save(client(data), **names)
    assert r.status_code == 400 and "Nothing was saved" in r.text
    assert stored(data) == {}
    if names == {"A": "x" * 61}:
        assert f'value="{"x" * 61}"' in r.text         # typed values kept


def test_no_speakers_and_missing_transcript(data):
    episode_with_speakers(data, segs=(Segment(0, 1, "no labels here"),))
    c = client(data)
    assert 'id="speakers"' not in c.get(f"/episodes/{VID}").text
    assert save(c, A="Host").status_code == 409 and stored(data) == {}
    transcript_path(data).unlink()
    assert save(c, A="Host").status_code == 409
    assert 'id="speakers"' not in c.get(f"/episodes/{VID}").text


def test_unknown_episode_404_and_get_changes_nothing(data):
    c = client(data)
    assert c.post("/episodes/zzzzzzzzzzz/speakers", content="name_A=x", headers=FORM).status_code == 404
    episode_with_speakers(data)
    assert c.get(f"/episodes/{VID}/speakers").status_code in (404, 405)


def test_saving_names_costs_nothing_and_queues_nothing(data):
    episode_with_speakers(data)
    before = jobs_and_spend(data)
    save(client(data), A="Host")
    assert jobs_and_spend(data) == before


def test_hostile_name_escaped(data):
    episode_with_speakers(data)
    c = client(data)
    save(c, A="<script>x</script>")
    for url in (f"/episodes/{VID}", f"/episodes/{VID}/transcript"):
        assert "<script>x</script>" not in c.get(url).text


def test_form_not_in_polled_fragment(data):
    episode_with_speakers(data)
    assert 'id="speakers"' not in client(data).get(f"/episodes/{VID}/status").text


# ---- pipeline ----------------------------------------------------------------------------

def test_summarize_and_verify_receive_names_and_version_stores_them(data):
    episode_with_speakers(data)
    save(client(data), A="Host", B="Peter Thiel")
    rec = run(data)
    for step in ("summarize", "verify"):
        assert [s.speaker for s in rec.seen[step].segments] == ["Host", "Peter Thiel", "Host"]
    with closing(db.connect(data)) as c:
        v = episodes.latest_one_pager_version(c, VID)
    assert v["speaker_names"] == {"A": "Host", "B": "Peter Thiel"}
    assert [s.speaker for s in Transcript.from_dict(
        artifacts.read_json(transcript_path(data))).segments] == ["A", "B", "A"]


def test_no_names_means_unchanged_pipeline(data):
    episode_with_speakers(data)
    rec = run(data)
    assert [s.speaker for s in rec.seen["summarize"].segments] == ["A", "B", "A"]
    with closing(db.connect(data)) as c:
        assert episodes.latest_one_pager_version(c, VID)["speaker_names"] is None


def test_quote_matching_still_finds_quotes_with_names(data):
    named = speakers.apply(Transcript(SEGS), {"A": "Host", "B": "Peter Thiel"})
    idx = QuoteIndex(named)
    assert idx.found("Peter Thiel: I think the future is stagnant and slow")
    assert idx.found("I think the future is stagnant and slow")
    assert not idx.found("I think the future is bright and fast")


def test_regenerate_uses_current_names_and_history_notes_the_change(data):
    episode_with_speakers(data)
    c = client(data)
    run(data)
    save(c, A="Host", B="Peter Thiel")
    import re
    page = c.get(f"/episodes/{VID}/regenerate").text
    assert "saved speaker names will be used" in page and "Host, Peter Thiel" in page
    est = re.search(r'name="estimate" value="(\d+)"', page).group(1)
    assert c.post(f"/episodes/{VID}/regenerate", content=f"estimate={est}",
                  headers=FORM).status_code == 303
    rec = run(data)
    assert [s.speaker for s in rec.seen["verify"].segments] == ["Host", "Peter Thiel", "Host"]
    with closing(db.connect(data)) as conn:
        versions = episodes.list_one_pager_versions(conn, VID)
    assert versions[0]["speaker_names"] is None
    assert versions[1]["speaker_names"] == {"A": "Host", "B": "Peter Thiel"}
    text = c.get(f"/episodes/{VID}").text
    assert "speaker names changed" in text
    assert c.get(f"/episodes/{VID}/versions/1").status_code == 200


def test_verify_uses_the_names_of_the_version_it_checks(data):
    episode_with_speakers(data)
    c = client(data)
    save(c, A="Host")
    run(data)
    save(c, A="Someone else")                    # changed after the version was written
    with closing(db.connect(data)) as conn:
        job = episodes.get_latest_job_with_steps(conn, VID)
        episodes.set_step_state(conn, job["id"], "verify", "pending")
        episodes.set_job_state(conn, job["id"], "queued")
        conn.execute("DELETE FROM fidelity_scores")
    rec = run(data)
    assert rec.seen["verify"].segments[0].speaker == "Host"


# ---- history summaries -------------------------------------------------------------------

H = {"summarize": "h"}


@pytest.mark.parametrize("prev,cur,expected", [
    ({"prompt_hashes": H, "model": "m"}, {"prompt_hashes": H, "model": "m"}, "no prompt change"),
    ({"prompt_hashes": H, "model": "m", "speaker_names": None},
     {"prompt_hashes": H, "model": "m", "speaker_names": {}}, "no prompt change"),
    ({"prompt_hashes": H, "model": "m"},
     {"prompt_hashes": H, "model": "m", "speaker_names": {"A": "x"}}, "speaker names changed"),
    ({"prompt_hashes": H, "model": "m", "speaker_names": {"A": "x"}},
     {"prompt_hashes": H, "model": "m", "speaker_names": {"A": "y"}}, "speaker names changed"),
    ({"prompt_hashes": {}, "model": "m"},
     {"prompt_hashes": H, "model": "m", "speaker_names": {"A": "x"}},
     "no prompt record; speaker names changed"),
    ({"prompt_hashes": H, "model": "a"},
     {"prompt_hashes": {"summarize": "z"}, "model": "b", "speaker_names": {"A": "x"}},
     "changed: summarize; model changed: a to b; speaker names changed"),
])
def test_change_summary_with_names(prev, cur, expected):
    assert change_summary(prev, cur) == expected


def test_migration_from_schema_11(tmp_path):
    import sqlite3
    from app.store import migrations
    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    for v, stmts in enumerate(migrations.MIGRATIONS[:11], start=1):
        for s in stmts:
            conn.execute(s)
        conn.execute(f"PRAGMA user_version = {v}")
    conn.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('a','u','t')")
    conn.execute("INSERT INTO one_pager_versions VALUES ('a', 1, 't', 'm', '{}')")
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert conn.execute("SELECT speaker_names FROM one_pager_versions").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM speaker_names").fetchone()[0] == 0
