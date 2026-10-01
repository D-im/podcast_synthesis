# Story 1.11: First supervised real run

Date: 2026-09-30. Episode: `B7yl7fEHeKM` (about 65.5 minutes, 3929 s, an English interview that opens in German). Providers: yt-dlp, AssemblyAI (`universal-3-5-pro`, speaker labels on), Claude Sonnet 5.5, no verifier. Run through the running app (`uv run podcast-synthesis`, form submit), with one manual recovery described below.

## Result in one line

The pipeline works end to end on a real hour-long episode. It cost **$0.31** (transcription $0.2511, one-pager $0.0587), finished with the verify step skipped, and produced a One-Pager that is faithful to the transcript, with some topics left out. The real run also exposed one slow-upload problem and one opaque error, both fixed (below).

## Cost (ledger, matches the Episode page)

| Step | Provider | Actual | Estimate | Notes |
|---|---|---|---|---|
| download | yt-dlp | $0.0000 | $0 | free |
| transcribe | assemblyai | $0.2511 | $0.2510 | 3929 s at $0.23 per hour; matches to the cent |
| summarize | anthropic | $0.0587 | "a few tens of cents" at most | single call, no map-reduce (estimate is about 19k tokens, far under the 150k limit) |
| verify | none | skipped | - | no verifier configured |
| **Total** | | **$0.3098** | | about $0.28 per hour of audio |

Ledger rows: `transcribe` with the AssemblyAI job ID as `provider_ref`, `summarize` with the Anthropic response ID. Not in the ledger: one manual 5-minute AssemblyAI test during diagnosis (about $0.019, billed outside the app) and the earlier smoke tests (a few cents). Total real spend for the story is roughly $0.35.

## Timing (wall clock)

- download: about 70 s.
- transcribe: dominated by the upload. The app's own upload of the 59 MB original ran about 16 minutes and then failed (see problems). After the fix, 16.8 MB of compressed audio uploads in about 220 s at the same link speed (about 75 KB/s). AssemblyAI finished processing the hour of audio in well under a minute once the upload completed.
- summarize: about 15 s.

## Transcript and One-Pager

- 142 segments, 10,378 words, 2 speakers (A interviewer 71 turns, B guest 71 turns), last segment ends at 3925 s. The opening is German, then English; code-switching was handled. Some names are garbled by the speech model ("Merck's"/"Mertz", "Greta").
- One-Pager v1: model `claude-sonnet-5-5`, one prompt hash recorded (`summarize`). Sections: Summary, Big ideas, Actionable items, Notable quotes, Worth your time. Stored at `data/episodes/B7yl7fEHeKM/one_pager.v1.json`; the Transcript is at `.../transcript.json`. Open `http://127.0.0.1:8765/episodes/B7yl7fEHeKM` after starting the app to read both.
- The Episode page showed Downloading, Transcribing, Summarizing, Done in order, with spend per step, and the library listed it.

## Faithfulness check (checked against the full transcript)

28 specific claims checked. **26 supported, 1 slightly softened, 0 unsupported or invented.** Quotes were checked word for word.

| # | Claim in the One-Pager | Verdict |
|---|---|---|
| 1 | Interview in Berlin; A interviews B, addressed as Peter; Axel Springer Award that evening; Palantir and PayPal co-founder | supported |
| 2 | Part of the transcript is German and some names are garbled | supported (and honest) |
| 3 | B partly changed his mind on stagnation; slowdown since the 1970s, bits not atoms | supported |
| 4 | AI may be bigger than the internet and could reaccelerate growth; nobody now disputes stagnation | supported |
| 5 | AI takeover odds "uncertain", but even 5 to 10% is bad | **softened**: B said he does not think the probabilities are very high, then added that 5 to 10% would still be bad |
| 6 | Slowing AI globally needs a world government, "a cure worse than the disease" | supported |
| 7 | The Pope's AI encyclical effectively helps the CCP | supported |
| 8 | Chinese anti-AI influence plausible but not the main driver; Western sentiment mostly self-inflicted | supported |
| 9 | Europe "extremely far behind" in AI, further behind than Minitel; little urgency in corporations | supported |
| 10 | Hypothesis: a "fear of success" in Germany | supported |
| 11 | Of Germany's 20 wealthiest Gen X or younger among its top 50, all inherited; in the US 9 of 12 made their own | supported (exact) |
| 12 | Real problem is incompetence ("bad"), not "evil"; does not want moral labels | supported |
| 13 | 2009 "freedom and democracy" line was about depoliticization, aimed at libertarians, corrected two weeks later | supported |
| 14 | AfD wrong on Ukraine, China and Israel; Linke and AfD reflect centrist failure and lack solutions | supported |
| 15 | Current Trump administration "not very smart but very loyal people" | supported |
| 16 | About 50% chance democratic socialists win in 2028; remains a Vance partisan | supported |
| 17 | Germany's Chancellor has no answer beyond borrowing more; expects continued drift | supported |
| 18 | Europe should be a reliable US ally; rejects moral equivalence of US and China | supported |
| 19 | Longevity: no clear natural limit, rejects mind uploading as "not me", wants to tackle dementia | supported |
| 20 | A frames technology as a neutral "knife" and health progress as purely positive | supported |
| 21 | Hiring question "What truth do you believe that almost nobody agrees with?" | supported (stated by A as B's habit) |
| 22 | Quote "I think we moralize way too much." | verbatim |
| 23 | Quote "The alternative is also far from neutral." | accurate, flagged as lightly adjusted ("I think the alternative is also far from neutral") |
| 24 | Quote "I don't think the point is to be contrarian for its own sake." | verbatim |
| 25 | Quote "...room for at least one generalist in a world where everyone's hyper-specialized." | verbatim |
| 26 | Quote on transhumanism "...it didn't go far enough." | accurate, flagged as lightly condensed |
| 27 | Timestamps for the four key segments (0:00-0:09, 0:40-0:46, 0:25-0:34, 0:56-1:03) | supported |
| 28 | "Actionable items" are implied, not formal advice | supported, and honestly labelled |

**Missed ideas** (in the transcript, absent from the One-Pager): the Argentina and Milei discussion (about 2 minutes); the deficit argument (three choices: cut spending, raise taxes, or keep borrowing, with 20 years of borrowing and interest no longer zero), where the One-Pager keeps only "borrow more"; the Berlin youth vote figures (Linke 25% overall, 47% among 16 to 24 year olds) and the "gerontocracy" point; the Fukuyama exchange; the "insider and outsider founders" theme; chess.

**Verdict for the core assumption:** For deciding whether to listen, and as a reference note, this One-Pager is trustworthy: nothing was invented, quotes are accurate, claims are attributed to the right speaker, and it flags garbled names itself. It is **not** a full replacement for the episode: it drops several sub-topics and some supporting arguments, and the Actionable items section is thin because the guest gave little advice. What would change that: a per-section length allowance in the prompt (the model kept to "about one page"), and the Epic 2 fidelity coverage check, which exists to flag exactly these omissions. The final call is Diimrem's: read the One-Pager, then judge against what you would have wanted from the episode.

## Log check

Searched the console log, the `data/` database dump and all project files (except `.env`) for the three API key values, and the log for distinctive transcript phrases. **None found.**

## Daily Cap recommendation

Measured: **$0.31 for a 65-minute episode, about $0.28 per hour of audio**. A 6-hour episode should cost about $1.40 to $1.70 (transcription $1.38 plus roughly $0.25 for the one-pager; map-reduce only above the token limit). The default $5 cap allows about 16 one-hour episodes or 3 six-hour ones. If you want a tighter guard, **$3** still covers about 9 one-hour episodes a day. The number stays in `config.toml` (`daily_cap_usd`) and is not enforced until Epic 3. Your call.

## Problems found and what was done

1. **Upload to AssemblyAI is slow here (about 75 KB/s)** although this connection's uplink measures about 15.8 Mbps. A direct HTTP upload gave the same speed, so it is the vendor endpoint or path, not our code. The 59 MB original would take about 13 minutes to send. **Fixed**: the adapter now re-encodes a temporary mono 16 kHz AAC copy (32 kbps, configurable) with ffmpeg and uploads that (16.8 MB for this episode, about 3.5 times less), then deletes it. The stored listening copy is untouched, and compression failure falls back to the original. A real 5-minute test through the adapter took 29 s instead of about 57 s.
2. **The app's own submit failed after about 16 minutes with an opaque "TranscriptError"** and no vendor job was created, so nothing was billed. The reason was hidden because the step message only held the exception class name. The same full-length audio, compressed, was accepted when sent by hand, so the cause is likely tied to the very long upload of the large file. **Fixed (diagnosability)**: the step message now includes the vendor's reason (URLs removed, only SDK and HTTP-library messages shown, never the key). The root cause stays unknown; if it recurs the message will say why.
3. **No retry exists yet (Epic 3).** After the failure the Job was put back to `queued` by hand with the transcription job's ID (submitted separately from the compressed audio) saved on the step. The app resumed that vendor job, did not upload again, recorded the cost once, and carried on. This was the first real use of the resume design (AD-5) and it worked.
4. **Port check:** during the Story 1.10 live check an unknown process was already listening on the app's port and was stopped by mistake (it may have been Diimrem's own run). For this run the port was confirmed free first, and only the process this run started was stopped.

## Acceptance criteria

- Job reaches `done` with a real Transcript and One-Pager: met.
- Actual cost per step and total recorded, matching the ledger: met.
- At least 8 claim verdicts and a plain trust statement: met (28 claims).
- No key or transcript text in logs: met.
- Offline suite with the changed defaults: 389 passed, 4 skipped.
