---
title: 'Story 1.1: Start the app with one command'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'NO_VCS'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The project has no code. Every later story needs a running local app with config, secrets handling and a data folder.

**Approach:** Create the greenfield Python project (uv, FastAPI, Uvicorn, Jinja2) in the layout the architecture spine fixes. One command starts a localhost-only server that serves a home page, bootstraps `data/` and SQLite, loads `config.toml`, reads secrets from the environment or `.env`, and reports missing keys or tools by name without crashing.

## Boundaries & Constraints

**Always:**
- Bind to `127.0.0.1` only. The host is not configurable and there is no auth.
- `app/core` and `app/web` never import `app/adapters`. All SQLite and file access is under `app/store`.
- SQLite opens in WAL mode. No tables are created in this story (Story 1.2 creates the first ones).
- Missing API keys, ffmpeg or a JavaScript runtime never stop startup. Each is reported by name, on the console and on the home page.
- Secrets are never printed or logged, including as a "set/unset" value dump.
- Decisions: start command `uv run podcast-synthesis`; default port 8765, set in `config.toml`; Python >=3.12; keys `ASSEMBLYAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`; JS runtime counts as present if `node` >=22 or `deno` >=2.3 is on PATH.

**Never:**
- No ORM, no Celery, no Redis, no JS framework, no authentication.
- No adapters, ports, worker, routes beyond the home page, or prompt files (later stories).
- Do not load `.env` values into any file or log. Do not commit `.env` or `data/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh start | No `data/`, valid `config.toml` | `data/` and `data/podcast_synthesis.db` created, WAL on, home page 200 | N/A |
| Restart | `data/` with existing DB | Same DB reused, nothing lost | N/A |
| Missing key | `OPENAI_API_KEY` unset | Starts. Console and home page name `OPENAI_API_KEY` | Never logs values |
| Missing tool | No ffmpeg or no JS runtime | Starts. Names the missing tool and where it looked (PATH) | N/A |
| `.env` present | `ASSEMBLYAI_API_KEY` in `.env` only | Treated as set. Real env var wins over `.env` | N/A |
| Bad config | `config.toml` missing or invalid TOML | Exits with a clear message naming the file and the problem | Non-zero exit |

</frozen-after-approval>

## Code Map

- Project is greenfield: only `_bmad/`, `_bmad-output/`, agent folders and a `.gitignore` exist.
- `.gitignore` -- append `.env`, `data/`, `.venv/`, `__pycache__/`, `.pytest_cache/`.
- Host has uv 0.12.19, Python 3.14, Node 26. ffmpeg and Deno are not installed, so the missing-tool report will show ffmpeg on this machine.

## Tasks & Acceptance

**Execution:**
- [ ] `pyproject.toml` -- project `podcast-synthesis`, Python >=3.12, deps fastapi, uvicorn, jinja2, python-dotenv, dev dep pytest and httpx, script `podcast-synthesis = app.main:run` -- one-command start
- [ ] `config.toml` -- port, `daily_cap_usd = 5.0`, provider and model names (assemblyai, anthropic, openai), `token_limit`, fidelity thresholds -- single config file
- [ ] `app/config.py` -- load and validate `config.toml` into a typed object; clear error on missing or invalid file -- config boundary
- [ ] `app/env.py` -- load `.env` without overriding real env; report which required keys are missing (names only) -- secrets handling
- [ ] `app/checks.py` -- detect ffmpeg, node >=22 or deno >=2.3 on PATH; return a list of missing tools with where checked -- host prerequisites
- [ ] `app/store/db.py` -- create `data/` and open SQLite in WAL mode; no tables -- bootstrap
- [ ] `app/web/` -- FastAPI app factory, Jinja2 home page listing startup warnings -- minimal UI
- [ ] `app/main.py` -- `run()` builds config, checks, store, prints warnings, starts Uvicorn on `127.0.0.1` -- entry point
- [ ] `.gitignore` -- append ignore entries above
- [ ] `prompts/.gitkeep` -- empty folder placeholder for Story 1.8
- [ ] `tests/` -- cover every row of the matrix using temp dirs and TestClient

**Acceptance Criteria:**
- Given a fresh checkout, when `uv run podcast-synthesis` runs, then the home page responds at `http://127.0.0.1:8765` and the socket is not reachable on other interfaces.
- Given no `data/`, when the app starts, then `data/` and the DB exist, `PRAGMA journal_mode` returns `wal`, and a second start keeps the data.
- Given a missing key or tool, when the app starts, then it names each one and keeps running.
- Given `.env` and `data/`, when checking `.gitignore`, then both are listed.
- Given the app's output, when it runs with keys set, then no key value appears in console output or logs.

## Implementation Notes

- Built as specified. Package `app/` with `config.py`, `env.py`, `checks.py`, `main.py`, `store/db.py`, `web/app.py` plus a Jinja2 home template, `config.toml`, `prompts/.gitkeep`, `.gitignore` additions, `pyproject.toml` (hatchling backend) and `tests/test_startup.py` (29 tests).
- Model names in `config.toml` follow the spine: `universal-3.5-pro`, `claude-sonnet-5-5`, `gpt-6.1-sol`. Fidelity keys are `accuracy_threshold` and `coverage_threshold` to match Story 2.2. Default thresholds (0.9, 0.75) and `token_limit` (150000) are placeholders to tune.
- Review patches applied: startup exits cleanly on unusable data folder; WAL is verified; config rejects NaN/inf cap, thresholds outside 0..1 and missing provider/model roles; empty env values no longer shadow `.env`; tests made hermetic and extended.
- Host: ffmpeg is not installed on this machine, so the home page lists it as missing. Needed before Story 1.6.
- No version control in this folder, so the review diff was built from the new files.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Startup crashes with traceback when data folder/DB unusable | medium | patch | `db.bootstrap()` errors were unhandled in `run()`; now exits 1 with message |
| WAL not verified after PRAGMA | medium | patch | PRAGMA result was ignored; spec AC requires WAL |
| NaN/inf cap, thresholds out of range, missing provider/model roles | medium | patch | Validation accepted them; later code would KeyError or misbehave |
| Empty env value shadows `.env` / `KEY=` loads empty | low | patch | Direct one-line correction |
| Config validation branches untested | medium | patch | Only invalid-TOML path was tested |
| Console and page warnings via `run()` not asserted; `.env` ordering; all-clear page; deno 2.2 boundary; run test depended on host | medium | patch | Tests could not catch removal of these behaviors; now covered hermetically |
| No README or `.env.example` documenting keys and setup | low | defer | Not in story scope, recorded in deferred-work |
| SQLite `foreign_keys`, `busy_timeout` not set | medium (unverified) | defer | No tables yet; decide in Story 1.2 and 1.3 when concurrent access begins |
| Missing `__init__.py` files | false | none | Files exist; empty files produce no diff hunk |
| `prompts/.gitkeep` missing / `.gitignore` overwritten | false | none | File exists; original four lines still present |
| Model names look invented | false | none | Match the web-verified names in the spine |
| Warnings frozen at startup | false | none | Spec requires a startup report only |
| Version parsing of `v22`, broken ffmpeg, wheel install root, connection leak on PRAGMA failure, extra gitignore entries | low | rejected | Unlikely in everyday use and fix adds complexity |
| `test_gitignore` tests text not git behavior | low | rejected | Acceptable for a no-VCS folder |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `uv run podcast-synthesis` then `curl -s http://127.0.0.1:8765/ | head` -- expected: HTML home page that lists ffmpeg as missing on this machine
- `sqlite3 data/podcast_synthesis.db "PRAGMA journal_mode;"` -- expected: `wal`
