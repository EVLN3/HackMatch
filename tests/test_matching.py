from hack_match.matching import (
    compatibility_score,
    compute_best_global_matches,
    compute_best_global_team_matches,
    team_compatibility_score,
)
from hack_match.quiz import QUESTIONS_BY_ID, Profile, Question, ScoringRule


def score_on(a_answers: dict, b_answers: dict, *question_ids: str) -> float:
    a = Profile(user_id=1, answers=a_answers)
    b = Profile(user_id=2, answers=b_answers)
    questions = [QUESTIONS_BY_ID[qid] for qid in question_ids]
    return compatibility_score(a, b, questions)


# No current quiz question uses ORDINAL_CLOSENESS; test the rule generically
# against a standalone question rather than relying on one existing.
_LEVEL_ORDER = ["Low", "Medium", "High"]
_LEVEL_QUESTION = Question(
    id="level", text="Level", options=_LEVEL_ORDER,
    scoring_rule=ScoringRule.ORDINAL_CLOSENESS, order=_LEVEL_ORDER,
)


def score_ordinal(a_value: str, b_value: str) -> float:
    a = Profile(user_id=1, answers={"level": a_value})
    b = Profile(user_id=2, answers={"level": b_value})
    return compatibility_score(a, b, [_LEVEL_QUESTION])


def test_aligned_scores_1_on_match_and_0_on_mismatch():
    assert score_on({"priority": "Winning"}, {"priority": "Winning"}, "priority") == 1.0
    assert score_on({"priority": "Winning"}, {"priority": "Learning"}, "priority") == 0.0


def test_overlap_scores_jaccard_similarity():
    a = {"technologies": ["Python", "React"]}
    b = {"technologies": ["Python", "Node.js"]}
    # shared: {Python} (1), union: {Python, React, Node.js} (3)
    assert score_on(a, b, "technologies") == 1 / 3


def test_overlap_scores_zero_when_one_side_empty():
    assert score_on({"technologies": []}, {"technologies": ["Python"]}, "technologies") == 0.0


def test_ordinal_closeness_rewards_nearby_answers_over_distant_ones():
    same = score_ordinal("Low", "Low")
    adjacent = score_ordinal("Low", "Medium")
    distant = score_ordinal("Low", "High")

    assert same == 1.0
    assert distant == 0.0
    assert same > adjacent > distant


def test_ordinal_closeness_does_not_crash_on_an_unknown_value():
    # Guards against a question's options changing after someone has already
    # answered it (their stored value no longer appears in `order`).
    assert score_ordinal("Extreme", "Low") == 0.0


def test_cross_match_rewards_mutual_gap_filling():
    # A's team already has a Backend dev and wants a Frontend dev;
    # B's team already has a Frontend dev and wants a Backend dev.
    team_a = {"role": ["Backend"], "looking_for": ["Frontend"]}
    team_b = {"role": ["Frontend"], "looking_for": ["Backend"]}
    assert score_on(team_a, team_b, "role") == 1.0

    team_c_wants_ai = {"role": ["Frontend"], "looking_for": ["AI/ML"]}
    assert score_on(team_a, team_c_wants_ai, "role") == 0.5


def test_custom_matrix_rewards_complementary_team_roles_over_clashing_ones():
    leader_builder = score_on({"team_role": "Leader"}, {"team_role": "Builder"}, "team_role")
    leader_leader = score_on({"team_role": "Leader"}, {"team_role": "Leader"}, "team_role")
    assert leader_builder > leader_leader


def test_idea_ownership_matrix_rewards_builder_joiner_pairing():
    builder_joiner = score_on(
        {"idea_ownership": "Build my own idea"},
        {"idea_ownership": "Join someone else's idea"},
        "idea_ownership",
    )
    both_builders = score_on(
        {"idea_ownership": "Build my own idea"},
        {"idea_ownership": "Build my own idea"},
        "idea_ownership",
    )
    assert builder_joiner > both_builders


def test_mentoring_matrix_rewards_mentor_mentee_pairing():
    mentor_mentee = score_on(
        {"mentoring": "Mentor someone less experienced"},
        {"mentoring": "Get guidance from someone more experienced"},
        "mentoring",
    )
    two_mentors = score_on(
        {"mentoring": "Mentor someone less experienced"},
        {"mentoring": "Mentor someone less experienced"},
        "mentoring",
    )
    assert mentor_mentee > two_mentors


def test_unanswered_and_informational_questions_are_excluded():
    # has_idea and looking_for are informational-only (scoring_rule=None);
    # a question neither profile answered should also be excluded.
    score = score_on(
        {"priority": "Winning", "has_idea": "Yes"},
        {"priority": "Winning", "mentoring": "Work independently"},
        "priority", "has_idea", "mentoring",
    )
    assert score == 1.0


def test_no_scoreable_questions_returns_zero():
    assert score_on({}, {}, "priority") == 0.0


def test_best_global_matches_maximizes_total_compatibility():
    leader = Profile(user_id=1, answers={"team_role": "Leader"})
    builder = Profile(user_id=2, answers={"team_role": "Builder"})
    designer = Profile(user_id=3, answers={"team_role": "Designer"})
    presenter = Profile(user_id=4, answers={"team_role": "Presenter"})

    # Leader+Builder (1.0) and Designer+Presenter (0.7) beats every other pairing
    # of these four, even though some other pair (e.g. Leader+Designer, 0.8) would
    # look better than Designer+Presenter in isolation.
    pairs = compute_best_global_matches(
        [leader, builder, designer, presenter], [QUESTIONS_BY_ID["team_role"]]
    )

    assert pairs[1] == 2
    assert pairs[2] == 1
    assert pairs[3] == 4
    assert pairs[4] == 3


def test_best_global_matches_can_leave_one_person_out_when_odd():
    a = Profile(user_id=1, answers={"team_role": "Leader"})
    b = Profile(user_id=2, answers={"team_role": "Builder"})
    c = Profile(user_id=3, answers={"team_role": "Builder"})

    pairs = compute_best_global_matches([a, b, c], [QUESTIONS_BY_ID["team_role"]])

    assert len(pairs) == 2
    unmatched = {1, 2, 3} - set(pairs.keys())
    assert len(unmatched) == 1


def test_team_compatibility_score_matches_individual_score_for_solo_teams():
    a = Profile(user_id=1, answers={"priority": "Winning"})
    b = Profile(user_id=2, answers={"priority": "Winning"})
    question = [QUESTIONS_BY_ID["priority"]]

    assert team_compatibility_score([a], [b], question) == compatibility_score(a, b, question)


def test_team_compatibility_score_merges_role_and_looking_for_across_members():
    # Team already covers Backend (p1) and Frontend (p2) between them.
    p1 = Profile(user_id=1, answers={"role": ["Backend"], "looking_for": ["AI/ML"]})
    p2 = Profile(user_id=2, answers={"role": ["Frontend"], "looking_for": ["AI/ML"]})
    # Candidate wants Backend (covered) and offers AI/ML (which the team wants).
    candidate = Profile(user_id=3, answers={"role": ["AI/ML"], "looking_for": ["Backend"]})

    score = team_compatibility_score([p1, p2], [candidate], [QUESTIONS_BY_ID["role"]])
    assert score == 1.0


def test_team_compatibility_score_averages_non_cross_match_questions_across_members():
    p1 = Profile(user_id=1, answers={"priority": "Winning"})
    p2 = Profile(user_id=2, answers={"priority": "Learning"})
    candidate = Profile(user_id=3, answers={"priority": "Winning"})

    # p1 matches candidate (1.0), p2 doesn't (0.0) -> averages to 0.5
    score = team_compatibility_score([p1, p2], [candidate], [QUESTIONS_BY_ID["priority"]])
    assert score == 0.5


def test_best_global_team_matches_respects_max_combined_size():
    # A 3-person and a 2-person team would exceed a cap of 4 if paired, so they
    # must never end up matched to each other, no matter how compatible they'd
    # otherwise look.
    team_of_3 = [Profile(user_id=i, answers={"priority": "Winning"}) for i in (1, 2, 3)]
    team_of_2 = [Profile(user_id=i, answers={"priority": "Winning"}) for i in (4, 5)]
    solo = [Profile(user_id=6, answers={"priority": "Winning"})]

    pairs = compute_best_global_team_matches(
        [team_of_3, team_of_2, solo], [QUESTIONS_BY_ID["priority"]], max_combined_size=4
    )

    assert pairs.get(1) != 4
    assert pairs.get(4) != 1


def test_best_global_team_matches_maximizes_total_compatibility():
    leader = Profile(user_id=1, answers={"team_role": "Leader"})
    builder = Profile(user_id=2, answers={"team_role": "Builder"})
    designer = Profile(user_id=3, answers={"team_role": "Designer"})
    presenter = Profile(user_id=4, answers={"team_role": "Presenter"})

    pairs = compute_best_global_team_matches(
        [[leader], [builder], [designer], [presenter]], [QUESTIONS_BY_ID["team_role"]]
    )

    assert pairs[1] == 2
    assert pairs[2] == 1
    assert pairs[3] == 4
    assert pairs[4] == 3
