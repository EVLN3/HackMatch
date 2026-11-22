"""SQLite-backed storage for quiz profiles, teams, and match proposals.

Everything is scoped per Discord server (guild_id), since the same bot can run
several hackathons at once (one per server) with entirely separate member
pools. The one exception is `team_proposals`, keyed by message_id alone (globally
unique on Discord) — but it still stores guild_id, since proposal messages are
DMs and DMs carry no guild context of their own; the guild has to come from here.
"""
import json
import sqlite3
from pathlib import Path

from hack_match.quiz import Profile

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "hackmatch.db"


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # WAL mode lets reads and writes happen concurrently instead of blocking;
    # busy_timeout makes a write retry for a bit instead of immediately
    # raising "database is locked" under concurrent access.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profiles ("
        "  user_id INTEGER NOT NULL,"
        "  guild_id INTEGER NOT NULL,"
        "  answers TEXT NOT NULL,"
        "  PRIMARY KEY (user_id, guild_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bot_state ("
        "  key TEXT NOT NULL,"
        "  guild_id INTEGER NOT NULL,"
        "  value TEXT NOT NULL,"
        "  PRIMARY KEY (key, guild_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS team_members ("
        "  user_id INTEGER NOT NULL,"
        "  guild_id INTEGER NOT NULL,"
        "  team_id INTEGER NOT NULL,"
        "  PRIMARY KEY (user_id, guild_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS team_proposals ("
        "  message_id INTEGER PRIMARY KEY,"
        "  guild_id INTEGER NOT NULL,"
        "  reactor_id INTEGER NOT NULL,"
        "  own_team_key TEXT NOT NULL,"
        "  candidate_key TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS team_approvals ("
        "  reactor_id INTEGER NOT NULL,"
        "  guild_id INTEGER NOT NULL,"
        "  candidate_key TEXT NOT NULL,"
        "  PRIMARY KEY (reactor_id, guild_id, candidate_key)"
        ")"
    )
    conn.commit()
    return conn


def save_profile(conn: sqlite3.Connection, profile: Profile, guild_id: int) -> None:
    conn.execute(
        "INSERT INTO profiles (user_id, guild_id, answers) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, guild_id) DO UPDATE SET answers = excluded.answers",
        (profile.user_id, guild_id, json.dumps(profile.answers)),
    )
    conn.commit()


def load_profile(conn: sqlite3.Connection, user_id: int, guild_id: int) -> Profile | None:
    row = conn.execute(
        "SELECT answers FROM profiles WHERE user_id = ? AND guild_id = ?", (user_id, guild_id)
    ).fetchone()
    if row is None:
        return None
    return Profile(user_id=user_id, answers=json.loads(row[0]))


def get_guilds_for_user(conn: sqlite3.Connection, user_id: int) -> list[int]:
    """Every server this user has signed up in (for DM commands with no guild
    context of their own, that need to know which hackathon to act on)."""
    rows = conn.execute("SELECT guild_id FROM profiles WHERE user_id = ?", (user_id,)).fetchall()
    return [r[0] for r in rows]


def load_all_profiles(conn: sqlite3.Connection, guild_id: int) -> list[Profile]:
    rows = conn.execute(
        "SELECT user_id, answers FROM profiles WHERE guild_id = ?", (guild_id,)
    ).fetchall()
    return [Profile(user_id=uid, answers=json.loads(answers)) for uid, answers in rows]


def has_matching_run(conn: sqlite3.Connection, guild_id: int) -> bool:
    row = conn.execute(
        "SELECT value FROM bot_state WHERE key = 'matching_run' AND guild_id = ?", (guild_id,)
    ).fetchone()
    return row is not None and row[0] == "true"


def mark_matching_run(conn: sqlite3.Connection, guild_id: int) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, guild_id, value) VALUES ('matching_run', ?, 'true') "
        "ON CONFLICT(key, guild_id) DO UPDATE SET value = 'true'",
        (guild_id,),
    )
    conn.commit()


def get_guild_deadline(conn: sqlite3.Connection, guild_id: int) -> str | None:
    """A per-server signup deadline override (ISO string), if one was set via
    /set_deadline. None means "use the .env default for every server"."""
    row = conn.execute(
        "SELECT value FROM bot_state WHERE key = 'deadline' AND guild_id = ?", (guild_id,)
    ).fetchone()
    return row[0] if row else None


def set_guild_deadline(conn: sqlite3.Connection, guild_id: int, deadline_iso: str) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, guild_id, value) VALUES ('deadline', ?, ?) "
        "ON CONFLICT(key, guild_id) DO UPDATE SET value = excluded.value",
        (guild_id, deadline_iso),
    )
    conn.commit()


def team_key(user_ids: list[int]) -> str:
    """A stable, order-independent identifier for a set of team members."""
    return ",".join(str(uid) for uid in sorted(user_ids))


def get_team_members(conn: sqlite3.Connection, user_id: int, guild_id: int) -> list[int]:
    """Everyone currently on user_id's team in this server (just [user_id] if solo)."""
    row = conn.execute(
        "SELECT team_id FROM team_members WHERE user_id = ? AND guild_id = ?", (user_id, guild_id)
    ).fetchone()
    if row is None:
        return [user_id]
    rows = conn.execute(
        "SELECT user_id FROM team_members WHERE team_id = ? AND guild_id = ?", (row[0], guild_id)
    ).fetchall()
    return [r[0] for r in rows]


def team_key_of(conn: sqlite3.Connection, user_id: int, guild_id: int) -> str:
    return team_key(get_team_members(conn, user_id, guild_id))


def clear_approvals_for(conn: sqlite3.Connection, user_ids: list[int], guild_id: int) -> None:
    """Invalidates these users' prior approvals in this server. Called whenever
    someone's team composition changes (merge or leave), so an old approval
    from before the change can never silently count toward a new proposal."""
    conn.executemany(
        "DELETE FROM team_approvals WHERE reactor_id = ? AND guild_id = ?",
        [(uid, guild_id) for uid in user_ids],
    )
    conn.commit()


def merge_teams(conn: sqlite3.Connection, user_ids: list[int], guild_id: int) -> None:
    """Union whichever teams these users currently belong to (in this server) into one."""
    all_members: set[int] = set()
    for uid in user_ids:
        all_members.update(get_team_members(conn, uid, guild_id))
    new_team_id = min(all_members)
    for uid in all_members:
        conn.execute(
            "INSERT INTO team_members (user_id, guild_id, team_id) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET team_id = excluded.team_id",
            (uid, guild_id, new_team_id),
        )
    conn.commit()
    clear_approvals_for(conn, list(all_members), guild_id)


def leave_team(conn: sqlite3.Connection, user_id: int, guild_id: int) -> list[int]:
    """Removes user_id from their current team in this server, going back to
    solo. Returns the remaining teammates (empty if there was no team to leave)."""
    members = get_team_members(conn, user_id, guild_id)
    if len(members) <= 1:
        return []
    remaining = [m for m in members if m != user_id]
    conn.execute("DELETE FROM team_members WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    if len(remaining) == 1:
        # Down to one person; drop their row too so they're solo, not a "team of 1" row.
        conn.execute(
            "DELETE FROM team_members WHERE user_id = ? AND guild_id = ?", (remaining[0], guild_id)
        )
    conn.commit()
    clear_approvals_for(conn, members, guild_id)  # user_id and everyone remaining
    return remaining


def load_teams_with_capacity(conn: sqlite3.Connection, guild_id: int, max_size: int) -> list[list[Profile]]:
    """This server's signed-up profiles grouped by current team, excluding
    teams already at max_size."""
    profiles = load_all_profiles(conn, guild_id)
    profiles_by_id = {p.user_id: p for p in profiles}
    seen: set[int] = set()
    teams: list[list[Profile]] = []
    for profile in profiles:
        if profile.user_id in seen:
            continue
        member_ids = get_team_members(conn, profile.user_id, guild_id)
        seen.update(member_ids)
        if len(member_ids) < max_size:
            teams.append([profiles_by_id[uid] for uid in member_ids if uid in profiles_by_id])
    return teams


def record_team_proposal(
    conn: sqlite3.Connection,
    message_id: int,
    guild_id: int,
    reactor_id: int,
    own_team_key: str,
    candidate_key: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO team_proposals "
        "(message_id, guild_id, reactor_id, own_team_key, candidate_key) VALUES (?, ?, ?, ?, ?)",
        (message_id, guild_id, reactor_id, own_team_key, candidate_key),
    )
    conn.commit()


def get_team_proposal(conn: sqlite3.Connection, message_id: int) -> tuple[int, int, str, str] | None:
    """Returns (guild_id, reactor_id, own_team_key, candidate_key). The proposal
    message is a DM, so the guild it belongs to has to come from here, not
    from the reaction event itself."""
    row = conn.execute(
        "SELECT guild_id, reactor_id, own_team_key, candidate_key FROM team_proposals "
        "WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    return tuple(row) if row else None


def get_proposals_for_candidate(
    conn: sqlite3.Connection, guild_id: int, candidate_key: str
) -> list[tuple[int, int]]:
    """(message_id, reactor_id) pairs for every pending post in this server
    that showed candidate_key as a candidate."""
    rows = conn.execute(
        "SELECT message_id, reactor_id FROM team_proposals WHERE guild_id = ? AND candidate_key = ?",
        (guild_id, candidate_key),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def record_team_approval(conn: sqlite3.Connection, reactor_id: int, guild_id: int, candidate_key: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO team_approvals (reactor_id, guild_id, candidate_key) VALUES (?, ?, ?)",
        (reactor_id, guild_id, candidate_key),
    )
    conn.commit()


def team_fully_approved(
    conn: sqlite3.Connection, member_ids: list[int], guild_id: int, candidate_key: str
) -> bool:
    """True if every one of member_ids has approved interest in candidate_key
    within this server."""
    rows = conn.execute(
        "SELECT reactor_id FROM team_approvals WHERE guild_id = ? AND candidate_key = ?",
        (guild_id, candidate_key),
    ).fetchall()
    approved = {r[0] for r in rows}
    return set(member_ids) <= approved
