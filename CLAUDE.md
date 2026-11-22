# HackMatch

## What this is
A Discord bot that matches hackers with teammates for an upcoming hackathon,
Tinder-style. Users answer a quiz about what they want in a teammate and a
bit about themselves; the bot uses those answers to suggest candidate teams
and posts them so users can react with an emoji to show interest ("swipe").
Matching happens at the **team** level (everyone starts as a solo "team of
1"): a merge only happens once every current member of *both* teams has
approved, and merged teams can keep matching with more people up to a cap
of 4 total.

## Project-specific notes
- This is a Discord bot: it needs a bot token and possibly other secrets
  (API keys, etc). Keep secrets in a `.env` file in this project folder,
  never committed or written into `data/`. Add `.env` to a `.gitignore` in
  this folder if/when this project becomes a git repo.
- Follow the layout and conventions in `C:\workspace\CLAUDE.md` (the parent
  rules file) — Python, type hints, PEP 8, `.venv`, `requirements.txt`,
  pytest, project structure.
- Quiz questions, profiles, team membership, and proposal/approval state
  live in a SQLite DB at `data\hackmatch.db` (`storage.py`), not JSON.
- **Everything is scoped per Discord server (`guild_id`)** — one bot can run
  several hackathons at once, one per server, with fully separate signup
  pools. Every storage table/function takes `guild_id` (profiles are keyed
  by `(user_id, guild_id)`, so the same person can sign up independently in
  each server they're in). The one exception: `team_proposals` is keyed by
  `message_id` alone (globally unique on Discord) but still *stores*
  `guild_id`, because candidate-proposal messages are DMs, and DMs carry no
  guild context of their own — `payload.guild_id` is always `None` for them,
  so the guild has to be looked up from what we stored, not from the event.
- Matching is deadline-gated, not on-demand or continuous: signups happen
  via `/quiz` any time before the deadline; a background task
  (`discord.ext.tasks.loop` in `bot.py`) checks every 5 minutes, loops over
  every server the bot is in, and runs that server's matching once after
  its deadline passes (tracked via a `bot_state` flag in SQLite, per guild).
  An admin can also run `/run_matching` any time, any number of times,
  regardless of the deadline. Each server's deadline is `.env`'s
  `SIGNUP_DEADLINE` by default, overridable per server via `/set_deadline`
  (admin-only; stored in `bot_state` under key `'deadline'`,
  `_effective_deadline()` in `bot.py` resolves override-vs-default).
- The `/quiz` command is a button-based wizard (Wordle-style): single-select
  questions advance immediately on click; multi-select ones toggle
  green/grey and need a "Next" button. "categories" caps at 3 picks via
  `Question.max_select`; "Other" on a multi-select opens a text modal
  instead of being a fixed option. The final step collects contact info
  (GitHub/LinkedIn/Instagram/Other) via a modal form (`free_text=True`),
  kept hidden from candidates until a team merge happens.
- Compatibility scoring is NOT just "same answer = more compatible". Each
  question in `quiz.py` is tagged with one of five `ScoringRule`s (see
  `matching.py`):
  - ALIGNED — same answer scores higher (e.g. `priority`, `availability`)
  - OVERLAP — multi-select; shared options score higher, via Jaccard
    similarity (e.g. `technologies`, `categories`)
  - ORDINAL_CLOSENESS — answers on a scale; closer values score higher, not
    just exact matches (no current question uses this, but the rule and its
    tests still exist for future use)
  - CROSS_MATCH — `role` (roles your team already has) should overlap with
    the other side's `looking_for`, and vice versa (mutual gap-filling)
  - CUSTOM_MATRIX — pairwise lookup table for cases that are neither aligned
    nor complementary (e.g. `team_role`: Leader+Builder pairs well, Leader+
    Leader clashes; `idea_ownership`: builder+joiner pairs well; `mentoring`:
    mentor+mentee pairs well, two mentors/two mentees is a mismatch)
  - A question can also have `scoring_rule=None` to be informational-only
    (not scored directly) — e.g. `has_idea`, `looking_for`, `contact`.

## Status
Core quiz, scoring, storage, and bot wiring are implemented (see README.md
for what's done vs. not). Not yet tested against a real Discord server.
