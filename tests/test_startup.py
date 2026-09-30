import os
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import checks, env, main
from app.config import ConfigError, load_config
from app.store import db
from app.web.app import create_app

GOOD = Path(__file__).resolve().parent.parent / "config.toml"


def test_fresh_start_creates_wal_db(tmp_path):
    d = tmp_path / "data"
    p = db.bootstrap(d)
    assert p.exists()
    assert sqlite3.connect(p).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = sqlite3.connect(p).execute(
        "select name from sqlite_master where type='table' order by name"
    ).fetchall()
    assert tables == [("episodes",), ("jobs",), ("steps",)]
    assert TestClient(create_app([])).get("/").status_code == 200


def test_restart_keeps_data(tmp_path):
    p = db.bootstrap(tmp_path)
    c = sqlite3.connect(p)
    c.execute("create table t(x)")
    c.execute("insert into t values (1)")
    c.commit()
    c.close()
    db.bootstrap(tmp_path)
    assert sqlite3.connect(p).execute("select x from t").fetchone() == (1,)


def test_missing_key_named_on_page_and_console(capsys):
    environ = {"ASSEMBLYAI_API_KEY": "sekret1", "ANTHROPIC_API_KEY": "sekret2"}
    w = main.collect_warnings(environ, path="")
    assert any("OPENAI_API_KEY" in x for x in w)
    html = TestClient(create_app(w)).get("/").text
    assert "OPENAI_API_KEY" in html
    assert "sekret" not in html and "sekret" not in str(w)


def test_missing_tools_named(tmp_path):
    missing = checks.check_tools(path=str(tmp_path))
    names = [m.name for m in missing]
    assert names == ["ffmpeg", "JavaScript runtime"]
    assert str(tmp_path) in missing[0].describe()


def _fake(dir, name, output):
    f = dir / name
    f.write_text(f"#!/bin/sh\necho '{output}'\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


def test_tools_present_and_versions(tmp_path):
    _fake(tmp_path, "ffmpeg", "ffmpeg version 7")
    _fake(tmp_path, "node", "v22.1.0")
    assert checks.check_tools(path=str(tmp_path)) == []
    old = tmp_path / "old"
    old.mkdir()
    _fake(old, "ffmpeg", "x")
    _fake(old, "node", "v20.0.0")
    assert [m.name for m in checks.check_tools(path=str(old))] == ["JavaScript runtime"]
    dn = tmp_path / "dn"
    dn.mkdir()
    _fake(dn, "ffmpeg", "x")
    _fake(dn, "deno", "deno 2.3.1 (stable)")
    assert checks.check_tools(path=str(dn)) == []
    old_deno = tmp_path / "old_deno"
    old_deno.mkdir()
    _fake(old_deno, "ffmpeg", "x")
    _fake(old_deno, "deno", "deno 2.2.9 (stable)")
    assert [m.name for m in checks.check_tools(path=str(old_deno))] == ["JavaScript runtime"]


def test_env_file_and_real_env_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("ASSEMBLYAI_API_KEY=fromfile\nOPENAI_API_KEY=fromfile\n")
    environ = {"OPENAI_API_KEY": "real"}
    env.load_env(f, environ)
    assert environ["ASSEMBLYAI_API_KEY"] == "fromfile"
    assert environ["OPENAI_API_KEY"] == "real"
    assert env.missing_keys(environ) == ["ANTHROPIC_API_KEY"]


def test_empty_values_do_not_shadow_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("ASSEMBLYAI_API_KEY=fromfile\nANTHROPIC_API_KEY=\n")
    environ = {"ASSEMBLYAI_API_KEY": ""}
    env.load_env(f, environ)
    assert environ["ASSEMBLYAI_API_KEY"] == "fromfile"
    assert "ANTHROPIC_API_KEY" not in environ


def test_bad_config(tmp_path):
    with pytest.raises(ConfigError, match="nope.toml"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("port = [")
    with pytest.raises(ConfigError, match="bad.toml.*invalid TOML"):
        load_config(bad)
    assert load_config(GOOD).port == 8765


def _config_text(**over):
    base = GOOD.read_text()
    for old, new in over.items():
        assert old in base, old
        base = base.replace(old, new)
    return base


@pytest.mark.parametrize(
    "old,new,key",
    [
        ("port = 8765", "port = 0", "port"),
        ("port = 8765", "port = 70000", "port"),
        ("port = 8765", "port = true", "port"),
        ("daily_cap_usd = 5.0", "daily_cap_usd = -1.0", "daily_cap_usd"),
        ("daily_cap_usd = 5.0", "daily_cap_usd = nan", "daily_cap_usd"),
        ("daily_cap_usd = 5.0", "daily_cap_usd = inf", "daily_cap_usd"),
        ("token_limit = 150000", "token_limit = 0", "token_limit"),
        ("accuracy_threshold = 0.9", "accuracy_threshold = 1.5", "accuracy_threshold"),
        ("coverage_threshold = 0.75", "coverage_threshold = -0.1", "coverage_threshold"),
        ('verifier = "openai"', "", "verifier"),
        ('summarizer = "claude-sonnet-5-5"', "", "summarizer"),
        ('transcriber = "universal-3.5-pro"', 'transcriber = ""', "transcriber"),
        ("[fidelity]", "[fidelity_x]", "fidelity"),
    ],
)
def test_invalid_config_values_name_the_key(tmp_path, old, new, key):
    f = tmp_path / "config.toml"
    f.write_text(_config_text(**{old: new}))
    with pytest.raises(ConfigError, match=key):
        load_config(f)


def test_valid_config_fields():
    c = load_config(GOOD)
    assert c.daily_cap_usd == 5.0 and c.token_limit > 0
    assert set(c.providers) >= {"transcriber", "summarizer", "verifier"}
    assert set(c.models) >= {"transcriber", "summarizer", "verifier"}
    assert 0 <= c.accuracy_threshold <= 1 and 0 <= c.coverage_threshold <= 1


def test_run_exits_nonzero_on_bad_config(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "config.toml"
    bad.write_text("port = [")
    monkeypatch.setattr(main, "load_config", lambda: load_config(bad))
    with pytest.raises(SystemExit) as e:
        main.run()
    assert e.value.code == 1
    assert "config.toml" in capsys.readouterr().err


def _run_with(monkeypatch, tmp_path, tools=(), env_loader=None):
    calls = {}
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kw: calls.update(kw, app=app))
    real = db.bootstrap
    monkeypatch.setattr(main.db, "bootstrap", lambda: real(tmp_path))
    monkeypatch.setattr(main.checks, "check_tools", lambda path=None: list(tools))
    monkeypatch.setattr(main, "load_config", lambda: load_config(GOOD))
    monkeypatch.setattr(main.env, "load_env", env_loader or (lambda: None))
    main.run()
    return calls


def test_run_binds_localhost_and_hides_secrets(monkeypatch, tmp_path, capsys):
    for k in env.REQUIRED_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "topsecretvalue")
    calls = _run_with(monkeypatch, tmp_path)
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8765
    out = capsys.readouterr()
    assert "topsecretvalue" not in out.out + out.err
    assert "WARNING: Missing API key: OPENAI_API_KEY" in out.out
    page = TestClient(calls["app"]).get("/").text
    assert "OPENAI_API_KEY" in page and "topsecretvalue" not in page


def test_run_reports_missing_tool_on_console_and_page(monkeypatch, tmp_path, capsys):
    tool = checks.check_tools(path="")[0]
    calls = _run_with(monkeypatch, tmp_path, tools=[tool])
    assert tool.name in capsys.readouterr().out
    assert tool.name in TestClient(calls["app"]).get("/").text


def test_run_loads_env_before_computing_warnings(monkeypatch, tmp_path, capsys):
    for k in env.REQUIRED_KEYS:
        monkeypatch.delenv(k, raising=False)

    def loader():
        for k in env.REQUIRED_KEYS:
            monkeypatch.setenv(k, "x")

    _run_with(monkeypatch, tmp_path, env_loader=loader)
    assert "Missing API key" not in capsys.readouterr().out


def test_home_page_all_clear():
    html = TestClient(create_app([])).get("/").text
    assert "All prerequisites present" in html and "Startup warnings" not in html


def test_run_exits_cleanly_when_data_folder_unusable(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "data"
    blocker.write_text("not a folder")
    monkeypatch.setattr(main, "load_config", lambda: load_config(GOOD))
    monkeypatch.setattr(main.env, "load_env", lambda: None)
    real = db.bootstrap
    monkeypatch.setattr(main.db, "bootstrap", lambda: real(blocker))
    with pytest.raises(SystemExit) as e:
        main.run()
    assert e.value.code == 1
    assert "Startup error" in capsys.readouterr().err


def test_gitignore_lists_env_and_data():
    lines = (GOOD.parent / ".gitignore").read_text().split()
    assert ".env" in lines and "data/" in lines
