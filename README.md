# HackMatch

A Discord bot that matches hackathon teammates, Tinder-style. Users run
`/quiz` (in the server) to answer a short quiz, Wordle-style — one question
per message, answered with buttons (multi-select ones turn green and need
a "Next" button). The last step collects contact info (GitHub/LinkedIn/
Instagram/Other) via a form, kept hidden until a match. After the signup
deadline passes (or an admin runs `/run_matching`, or anyone runs
`/more_matches` for just themselves), the bot DMs candidates as embeds —
"🌟 Your best match" (with a `## server name` heading) for your single
globally-best pick, "Potential teammate(s)" for the rest — with a
green→yellow→orange→red side color by team size (1 through full).

Matching works at the **team** level, not just 1-to-1: everyone starts as a
solo "team of 1." Reacting 🤝 to a candidate team records your approval, but
a merge only happens once **every current member of both teams** has
approved — then the two teams combine into one, automatically get shown a
fresh batch of candidates, and can keep matching with more people, up to a
cap of 4 total. Contact info is revealed to everyone involved once a merge
happens.

Other commands: `/unpair` (leave your team) and `/my_team` (see your
current roster) — both server-only. `/run_matching` is admin-only and runs
full matching for everyone in the server. `/more_matches` is the only
self-serve option for regular users — DM-only (since a DM has no server of
its own, it asks you to pick one if you've signed up in more than one),
gets a fresh batch for just your own team.

**Multi-server**: one bot can run several hackathons at once — every
server (guild) it's added to has its own fully separate pool of signups,
teams, and matches. The same Discord user can sign up independently in
each server they're in. Each server can also set its own signup deadline
with `/set_deadline` (admin-only), overriding the shared `.env` default.

Compatibility scoring depends on the question — see `quiz.py` and
`matching.py` for the five scoring rules (aligned, overlap, ordinal
closeness, cross-match, custom matrix).

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
copy .env.example .env
```

Then edit `.env`:
- `DISCORD_BOT_TOKEN` — your bot's token
- `SIGNUP_DEADLINE` — ISO datetime (e.g. `2026-12-05T18:00:00+00:00`), the
  default deadline for any server that hasn't set its own via `/set_deadline`
- `DEV_GUILD_ID` (optional) — a server ID to sync slash commands to
  instantly while developing/testing, instead of waiting on Discord's
  global sync (which can take up to an hour). Get it via Discord Developer
  Mode → right-click your server icon → Copy Server ID.

Your bot needs the `applications.commands` and `bot` scopes, and the
"Server Members" privileged intent enabled in the Discord developer portal,
to use `/quiz` and reactions.

## Run

```
.venv\Scripts\python -m hack_match.main
```

## Test

```
.venv\Scripts\pytest
```

## Status

- `quiz.py` — quiz questions + `Profile` data model, each question tagged
  with a scoring rule (or `free_text=True` for the contact-info step)
- `matching.py` — compatibility scoring per rule type; `team_compatibility_score`
  and `compute_best_global_team_matches` generalize this to teams of 1+
  people (role/looking_for merge via union across members; other questions
  average across every member-pair); a `max_combined_size` cap keeps two
  teams from being matched if merging would exceed the team-size limit.
  Pure functions with no notion of "server" — guild scoping lives entirely
  in `storage.py`/`bot.py`.
- `storage.py` — SQLite-backed profiles, team membership, approval tracking,
  and matching/deadline state, every table scoped by `guild_id` so each
  Discord server's hackathon stays fully independent (profiles are keyed by
  `(user_id, guild_id)`, so the same person can sign up separately per server)
- `bot.py` — `/quiz`, `/unpair`, `/my_team` (server-only), `/run_matching`
  (server-only, self-serve), `/more_matches` (DM-only, self-serve, with a
  `GuildPickerView` if signed up in more than one server), `/set_deadline`
  (admin, per-server override), a background task that checks every server
  it's in and runs *full* matching (every team, not just one) once
  automatically after that server's deadline passes, and reaction handling
  requiring unanimous approval from both teams before merging (updating
  the candidate embed's color/text live if a proposal goes stale — team
  changed or filled up — instead of silently ignoring it)
- Not yet done: no re-run if someone signs up after the deadline, minimal
  error handling around Discord API failures (rate limits, blocked DMs,
  etc.)
