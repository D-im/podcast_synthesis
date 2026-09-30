import ast
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.config import PROJECT_ROOT
from app.core import submit
from app.core.meter import StepMeter, format_usd, local_day_bounds_utc, to_micro
from app.store import db, episodes, migrations, spend
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    with closing(db.connect(tmp_path)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    return tmp_path


def rows(data):
    with closing(db.connect(data)) as c:
        return c.execute(
            "select video_id, step, provider, amount_micro_usd, created_at "
            "from spend_ledger order by id").fetchall()


def run(data, **kw):
    Worker(data, build_fakes(FakeBehavior(**kw))).run_next()


def test_charge_recorded_once_with_utc_stamp(data):
    run(data, cost_micro=250000)
    r = [x for x in rows(data) if x[1] == "transcribe"]
    assert len(r) == 1 and r[0][:4] == (VID, "transcribe", "fake", 250000)
    datetime.strptime(r[0][4], "%Y-%m-%dT%H:%M:%S.%fZ")


def test_zero_cost_writes_nothing(data):
    run(data, cost_micro=0)
    assert rows(data) == []


def test_episode_breakdown_and_total(data):
    run(data, cost_micro=250000)
    t = TestClient(create_app([], data)).get(f"/episodes/{VID}/status").text
    assert "transcribe: done - $0.2500" in t and "download: done - $0.2500" in t
    assert "<strong>$1.0000</strong>" in t


def test_no_spend_shows_zero(data):
    t = TestClient(create_app([], data)).get(f"/episodes/{VID}/status").text
    assert "<strong>$0.0000</strong>" in t


def test_charge_then_fail_keeps_row_and_shows_spend(data):
    run(data, cost_micro=100, fail_at="transcribe")
    assert [x[1] for x in rows(data)] == ["download", "transcribe"]
    with closing(db.connect(data)) as c:
        steps = {s["name"]: s["state"] for s in episodes.get_latest_job_with_steps(c, VID)["steps"]}
    assert steps["transcribe"] == "failed"
    page = TestClient(create_app([], data)).get(f"/episodes/{VID}").text
    assert "$0.0002" in page and "Failed at transcribe" in page


def test_header_on_every_page(data):
    client = TestClient(create_app([], data, 5.0))
    for url in ("/", f"/episodes/{VID}"):
        assert "Today: $0.00 of $5.00" in client.get(url).text
    run(data, cost_micro=250000)
    assert "Today: $1.00 of $5.00" in client.get("/").text


def test_header_on_error_page(data):
    client = TestClient(create_app([], data))
    r = client.post("/submit", data={"url": "nope"})
    assert r.status_code == 400 and "Today: $" in r.text


@pytest.mark.parametrize("bad", [-1, 1.5, True, "5", None])
def test_bad_amount_rejected(data, bad):
    with closing(db.connect(data)) as c:
        with pytest.raises(ValueError):
            StepMeter(c, VID, "download").record("fake", bad)
    assert rows(data) == []


def test_append_only(data):
    with closing(db.connect(data)) as c:
        StepMeter(c, VID, "download").record("fake", 5)
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("update spend_ledger set amount_micro_usd = 1")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("delete from spend_ledger")
    assert [x[3] for x in rows(data)] == [5]


def test_money_helpers():
    assert to_micro(0.0000005) == 1 and to_micro(1.25) == 1250000 and to_micro(5) == 5000000
    assert format_usd(250000) == "$0.2500" and format_usd(1250, 4) == "$0.0013"
    assert format_usd(5_000_000, 2) == "$5.00" and format_usd(4999, 2) == "$0.00"
    assert format_usd(5000, 2) == "$0.01"
    assert format_usd(123456789) == "$123.4568"


@pytest.fixture
def tz():
    old = os.environ.get("TZ")
    yield lambda name: (os.environ.__setitem__("TZ", name), time.tzset())
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_today_counts_only_local_day(data, tz):
    tz("America/New_York")
    now = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)  # 11:00 local, EDT
    start, end = local_day_bounds_utc(now)
    assert start == datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)
    local = lambda h, m, d: datetime(2026, 3, d, h, m).astimezone()
    with closing(db.connect(data)) as c:
        for moment, amount in ((local(23, 30, 9), 1), (local(0, 30, 10), 10),
                               (local(23, 30, 10), 100), (local(0, 30, 11), 1000)):
            spend.append(c, VID, "download", "fake", amount, moment)
        assert spend.total_between(c, start, end) == 110


def test_day_bounds_across_dst(tz):
    tz("America/New_York")
    start, end = local_day_bounds_utc(datetime(2026, 3, 8, 18, 0, tzinfo=timezone.utc))
    assert end - start == timedelta(hours=23)  # spring-forward day
    start, end = local_day_bounds_utc(datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc))
    assert end - start == timedelta(hours=25)


def test_upgrade_from_populated_v2(tmp_path):
    conn = sqlite3.connect(tmp_path / db.DB_NAME, isolation_level=None)
    for stmts in migrations.MIGRATIONS[:2]:
        for s in stmts:
            conn.execute(s)
    conn.execute("PRAGMA user_version = 2")
    conn.execute("insert into episodes (video_id,url,created_at) values ('x','u','n')")
    conn.close()
    db.bootstrap(tmp_path)
    with closing(db.connect(tmp_path)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 5
        assert c.execute("select count(*) from episodes").fetchone()[0] == 1
        assert c.execute("select count(*) from sqlite_master where type='trigger'"
                         " and tbl_name='spend_ledger'").fetchone()[0] == 2
        assert c.execute("select count(*) from spend_ledger").fetchone()[0] == 0


def test_single_writer_source_scan():
    app = PROJECT_ROOT / "app"
    for f in app.rglob("*.py"):
        text = f.read_text()
        rel = f.relative_to(app).as_posix()
        if rel != "store/spend.py":
            assert "insert into spend_ledger" not in " ".join(text.lower().split()), rel
        if rel in ("store/spend.py", "core/meter.py"):
            continue
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("store.spend"):
                assert "append" not in [a.name for a in n.names], rel
            assert not (isinstance(n, ast.Attribute) and n.attr == "append"
                        and isinstance(n.value, ast.Name) and n.value.id == "spend"), rel


@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_bad_provider_rejected(data, bad):
    with closing(db.connect(data)) as c:
        with pytest.raises(ValueError):
            StepMeter(c, VID, "download").record(bad, 5)
    assert rows(data) == []


def test_header_degrades_to_unavailable_when_ledger_read_fails(data, monkeypatch):
    client = TestClient(create_app([], data, 5.0))

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(spend, "total_between", boom)
    for path in ("/", f"/episodes/{VID}"):
        r = client.get(path)
        assert r.status_code == 200
        assert "Today: unavailable (cap $5.00)" in r.text
