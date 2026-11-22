from hack_match import storage
from hack_match.quiz import Profile

GUILD = 100
OTHER_GUILD = 200


def test_save_and_load_profile(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Backend"}), GUILD)

    assert storage.load_profile(conn, 1, GUILD).answers == {"role": "Backend"}


def test_load_profile_returns_none_when_missing(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.load_profile(conn, 999, GUILD) is None


def test_save_profile_overwrites_existing(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Backend"}), GUILD)
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Frontend"}), GUILD)

    assert storage.load_profile(conn, 1, GUILD).answers == {"role": "Frontend"}


def test_load_all_profiles(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Backend"}), GUILD)
    storage.save_profile(conn, Profile(user_id=2, answers={"role": "Frontend"}), GUILD)

    profiles = {p.user_id: p.answers for p in storage.load_all_profiles(conn, GUILD)}
    assert profiles == {1: {"role": "Backend"}, 2: {"role": "Frontend"}}


def test_same_user_has_independent_profiles_per_guild(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Backend"}), GUILD)
    storage.save_profile(conn, Profile(user_id=1, answers={"role": "Frontend"}), OTHER_GUILD)

    assert storage.load_profile(conn, 1, GUILD).answers == {"role": "Backend"}
    assert storage.load_profile(conn, 1, OTHER_GUILD).answers == {"role": "Frontend"}
    assert {p.user_id for p in storage.load_all_profiles(conn, GUILD)} == {1}
    assert {p.user_id for p in storage.load_all_profiles(conn, OTHER_GUILD)} == {1}


def test_get_guilds_for_user(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.get_guilds_for_user(conn, 1) == []

    storage.save_profile(conn, Profile(user_id=1, answers={}), GUILD)
    assert storage.get_guilds_for_user(conn, 1) == [GUILD]

    storage.save_profile(conn, Profile(user_id=1, answers={}), OTHER_GUILD)
    assert set(storage.get_guilds_for_user(conn, 1)) == {GUILD, OTHER_GUILD}


def test_matching_run_flag(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.has_matching_run(conn, GUILD) is False

    storage.mark_matching_run(conn, GUILD)
    assert storage.has_matching_run(conn, GUILD) is True
    assert storage.has_matching_run(conn, OTHER_GUILD) is False


def test_guild_deadline_override(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.get_guild_deadline(conn, GUILD) is None

    storage.set_guild_deadline(conn, GUILD, "2026-12-05T18:00:00+00:00")
    assert storage.get_guild_deadline(conn, GUILD) == "2026-12-05T18:00:00+00:00"
    assert storage.get_guild_deadline(conn, OTHER_GUILD) is None


def test_solo_user_has_team_of_just_themselves(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.get_team_members(conn, 1, GUILD) == [1]


def test_merge_teams_unions_solo_individuals(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.merge_teams(conn, [1, 2], GUILD)

    assert set(storage.get_team_members(conn, 1, GUILD)) == {1, 2}
    assert set(storage.get_team_members(conn, 2, GUILD)) == {1, 2}


def test_merge_teams_can_merge_two_existing_teams(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.merge_teams(conn, [1, 2], GUILD)
    storage.merge_teams(conn, [3, 4], GUILD)
    storage.merge_teams(conn, [2, 3], GUILD)

    assert set(storage.get_team_members(conn, 1, GUILD)) == {1, 2, 3, 4}
    assert set(storage.get_team_members(conn, 4, GUILD)) == {1, 2, 3, 4}


def test_teams_are_independent_per_guild(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.merge_teams(conn, [1, 2], GUILD)

    # Same two users, but no merge recorded in the other guild.
    assert storage.get_team_members(conn, 1, OTHER_GUILD) == [1]
    assert storage.get_team_members(conn, 2, OTHER_GUILD) == [2]


def test_leave_team_returns_empty_when_solo(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    assert storage.leave_team(conn, 1, GUILD) == []


def test_leave_team_removes_just_that_member(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.merge_teams(conn, [1, 2, 3], GUILD)

    remaining = storage.leave_team(conn, 2, GUILD)

    assert set(remaining) == {1, 3}
    assert set(storage.get_team_members(conn, 1, GUILD)) == {1, 3}
    assert storage.get_team_members(conn, 2, GUILD) == [2]  # back to solo


def test_leave_team_dissolves_pair_back_to_two_solos(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.merge_teams(conn, [1, 2], GUILD)

    remaining = storage.leave_team(conn, 1, GUILD)

    assert remaining == [2]
    assert storage.get_team_members(conn, 1, GUILD) == [1]
    assert storage.get_team_members(conn, 2, GUILD) == [2]


def test_load_teams_with_capacity_excludes_full_teams(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    for uid in [1, 2, 3, 4, 5]:
        storage.save_profile(conn, Profile(user_id=uid, answers={}), GUILD)
    storage.merge_teams(conn, [1, 2, 3, 4], GUILD)  # full team at max_size=4

    teams = storage.load_teams_with_capacity(conn, GUILD, max_size=4)
    team_member_sets = [set(p.user_id for p in team) for team in teams]

    assert {1, 2, 3, 4} not in team_member_sets
    assert {5} in team_member_sets


def test_team_proposal_roundtrip(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.record_team_proposal(
        conn, message_id=42, guild_id=GUILD, reactor_id=1, own_team_key="1", candidate_key="2"
    )

    assert storage.get_team_proposal(conn, 42) == (GUILD, 1, "1", "2")
    assert storage.get_team_proposal(conn, 999) is None


def test_get_proposals_for_candidate(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.record_team_proposal(
        conn, message_id=1, guild_id=GUILD, reactor_id=10, own_team_key="10", candidate_key="99"
    )
    storage.record_team_proposal(
        conn, message_id=2, guild_id=GUILD, reactor_id=11, own_team_key="11", candidate_key="99"
    )
    storage.record_team_proposal(
        conn, message_id=3, guild_id=GUILD, reactor_id=12, own_team_key="12", candidate_key="other"
    )

    results = set(storage.get_proposals_for_candidate(conn, GUILD, "99"))
    assert results == {(1, 10), (2, 11)}
    assert storage.get_proposals_for_candidate(conn, GUILD, "nonexistent") == []
    assert storage.get_proposals_for_candidate(conn, OTHER_GUILD, "99") == []


def test_team_fully_approved_requires_every_member(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="3,4")
    assert storage.team_fully_approved(conn, [1, 2], GUILD, "3,4") is False

    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="3,4")
    assert storage.team_fully_approved(conn, [1, 2], GUILD, "3,4") is True


def test_approvals_are_independent_per_guild(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="2")
    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="1")

    assert storage.team_fully_approved(conn, [1], GUILD, "2") is True
    # Same two user_ids, but in a different server's pool -- must not count.
    assert storage.team_fully_approved(conn, [1], OTHER_GUILD, "2") is False


def test_full_reaction_protocol_merges_teams_only_once_both_sides_fully_approve(tmp_path):
    """Mirrors exactly what on_raw_reaction_add does, without any Discord objects."""
    conn = storage.connect(tmp_path / "test.db")

    # Two solo people (1, 2) mutually approve -> merge into a team.
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="2")
    assert not (
        storage.team_fully_approved(conn, [1], GUILD, "2")
        and storage.team_fully_approved(conn, [2], GUILD, "1")
    )
    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="1")
    assert storage.team_fully_approved(conn, [1], GUILD, "2") and storage.team_fully_approved(
        conn, [2], GUILD, "1"
    )
    storage.merge_teams(conn, [1, 2], GUILD)
    assert set(storage.get_team_members(conn, 1, GUILD)) == {1, 2}

    # A solo person (3) wants to join the now 2-person team; BOTH current
    # members (1 and 2) must approve before the merge happens.
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="3")
    assert storage.team_fully_approved(conn, [1, 2], GUILD, "3") is False

    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="3")
    storage.record_team_approval(
        conn, reactor_id=3, guild_id=GUILD, candidate_key=storage.team_key([1, 2])
    )
    assert storage.team_fully_approved(conn, [1, 2], GUILD, "3") is True
    assert storage.team_fully_approved(conn, [3], GUILD, storage.team_key([1, 2])) is True

    storage.merge_teams(conn, [1, 2, 3], GUILD)
    assert set(storage.get_team_members(conn, 2, GUILD)) == {1, 2, 3}


def test_merging_clears_prior_approvals(tmp_path):
    conn = storage.connect(tmp_path / "test.db")
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="2")
    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="1")
    storage.merge_teams(conn, [1, 2], GUILD)

    assert storage.team_fully_approved(conn, [1], GUILD, "2") is False
    assert storage.team_fully_approved(conn, [2], GUILD, "1") is False


def test_unpair_then_repropose_requires_fresh_mutual_approval(tmp_path):
    """Regression test for a real bug: after A and B merge then unpair, a
    stale approval from before the unpair must NOT let a single new reaction
    silently re-trigger a merge between the same two people."""
    conn = storage.connect(tmp_path / "test.db")

    # 1 and 2 mutually approve and merge.
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="2")
    storage.record_team_approval(conn, reactor_id=2, guild_id=GUILD, candidate_key="1")
    storage.merge_teams(conn, [1, 2], GUILD)

    # 1 leaves; both are solo again.
    storage.leave_team(conn, 1, GUILD)
    assert storage.get_team_members(conn, 1, GUILD) == [1]
    assert storage.get_team_members(conn, 2, GUILD) == [2]

    # They get re-proposed to each other. Only 1 reacts this time.
    storage.record_team_approval(conn, reactor_id=1, guild_id=GUILD, candidate_key="2")

    # 2 must NOT already appear approved just because they approved before the unpair.
    assert storage.team_fully_approved(conn, [2], GUILD, "1") is False
