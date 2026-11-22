"""Compatibility scoring between two quiz profiles."""
import networkx as nx

from hack_match.quiz import Profile, Question, ScoringRule


def _overlap_score(a_answer: list[str], b_answer: list[str]) -> float:
    a_set, b_set = set(a_answer), set(b_answer)
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def _ordinal_score(a_answer: str, b_answer: str, order: list[str]) -> float:
    # An answer saved under a since-removed option (e.g. quiz options changed
    # after someone signed up) has no position on the scale; treat as no match.
    if a_answer not in order or b_answer not in order:
        return 0.0
    a_idx, b_idx = order.index(a_answer), order.index(b_answer)
    max_distance = len(order) - 1
    if max_distance == 0:
        return 1.0
    return 1 - abs(a_idx - b_idx) / max_distance


def _matrix_score(a_answer: str, b_answer: str, matrix: dict[tuple[str, str], float]) -> float:
    return matrix.get((a_answer, b_answer), matrix.get((b_answer, a_answer), 0.0))


def _cross_match_score(a: Profile, b: Profile, question: Question) -> float:
    """Mutual gap-filling: does each side's `question.id` (what they already
    have) cover something the other side's `cross_field` (what they want)?"""
    a_has = set(a.answers.get(question.id, []))
    b_has = set(b.answers.get(question.id, []))
    a_wants = set(a.answers.get(question.cross_field, []))
    b_wants = set(b.answers.get(question.cross_field, []))
    a_fills_b = bool(a_has & b_wants)
    b_fills_a = bool(b_has & a_wants)
    return (int(a_fills_b) + int(b_fills_a)) / 2


def _question_score(a: Profile, b: Profile, question: Question) -> float:
    a_answer, b_answer = a.answers[question.id], b.answers[question.id]

    if question.scoring_rule == ScoringRule.ALIGNED:
        return float(a_answer == b_answer)
    if question.scoring_rule == ScoringRule.OVERLAP:
        return _overlap_score(a_answer, b_answer)
    if question.scoring_rule == ScoringRule.ORDINAL_CLOSENESS:
        return _ordinal_score(a_answer, b_answer, question.order)
    if question.scoring_rule == ScoringRule.CUSTOM_MATRIX:
        return _matrix_score(a_answer, b_answer, question.matrix)
    if question.scoring_rule == ScoringRule.CROSS_MATCH:
        return _cross_match_score(a, b, question)
    raise ValueError(f"Unhandled scoring rule: {question.scoring_rule}")


def compatibility_score(a: Profile, b: Profile, questions: list[Question]) -> float:
    """Average per-question score (0.0-1.0) over questions both profiles answered."""
    scored_questions = [
        q for q in questions
        if q.scoring_rule is not None and q.id in a.answers and q.id in b.answers
    ]
    if not scored_questions:
        return 0.0

    total = sum(_question_score(a, b, q) for q in scored_questions)
    return total / len(scored_questions)


def compute_best_global_matches(profiles: list[Profile], questions: list[Question]) -> dict[int, int]:
    """The single best partner for each person, chosen to maximize total
    compatibility across everyone at once (not just each person's own top pick).
    Returns {user_id: partner_user_id} both ways; someone is left out only if
    there's an odd number of profiles."""
    graph = nx.Graph()
    graph.add_nodes_from(profile.user_id for profile in profiles)
    for i, a in enumerate(profiles):
        for b in profiles[i + 1:]:
            graph.add_edge(a.user_id, b.user_id, weight=compatibility_score(a, b, questions))

    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
    pairs: dict[int, int] = {}
    for a_id, b_id in matching:
        pairs[a_id] = b_id
        pairs[b_id] = a_id
    return pairs


def _team_cross_match_score(team_a: list[Profile], team_b: list[Profile], question: Question) -> float:
    """Team version: role/looking_for are merged (union) across every member
    of each team before checking mutual gap-filling."""
    a_has: set[str] = set().union(*(set(p.answers.get(question.id, [])) for p in team_a))
    b_has: set[str] = set().union(*(set(p.answers.get(question.id, [])) for p in team_b))
    a_wants: set[str] = set().union(*(set(p.answers.get(question.cross_field, [])) for p in team_a))
    b_wants: set[str] = set().union(*(set(p.answers.get(question.cross_field, [])) for p in team_b))
    a_fills_b = bool(a_has & b_wants)
    b_fills_a = bool(b_has & a_wants)
    return (int(a_fills_b) + int(b_fills_a)) / 2


def team_compatibility_score(team_a: list[Profile], team_b: list[Profile], questions: list[Question]) -> float:
    """Compatibility between two teams (each 1+ members). role/looking_for are
    merged (union) across each team; every other scored question is averaged
    over all member-pairs between the two teams who both answered it."""
    per_question_scores = []
    for question in questions:
        if question.scoring_rule is None:
            continue
        if question.scoring_rule == ScoringRule.CROSS_MATCH:
            per_question_scores.append(_team_cross_match_score(team_a, team_b, question))
            continue
        pair_scores = [
            _question_score(a, b, question)
            for a in team_a for b in team_b
            if question.id in a.answers and question.id in b.answers
        ]
        if pair_scores:
            per_question_scores.append(sum(pair_scores) / len(pair_scores))

    if not per_question_scores:
        return 0.0
    return sum(per_question_scores) / len(per_question_scores)


def compute_best_global_team_matches(
    teams: list[list[Profile]], questions: list[Question], max_combined_size: int | None = None
) -> dict[int, int]:
    """The single best other team for each team, chosen to maximize total
    compatibility across all teams at once. Teams are keyed by the lowest
    user_id among their members (a stable id, consistent with storage.merge_teams).
    Two teams are never paired if their combined size would exceed
    max_combined_size. Returns {team_id: partner_team_id} both ways; a team is
    left out if there's an odd number of teams, or no eligible partner fits."""
    team_ids = [min(p.user_id for p in team) for team in teams]
    graph = nx.Graph()
    graph.add_nodes_from(team_ids)
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            if max_combined_size is not None and len(teams[i]) + len(teams[j]) > max_combined_size:
                continue
            weight = team_compatibility_score(teams[i], teams[j], questions)
            graph.add_edge(team_ids[i], team_ids[j], weight=weight)

    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
    pairs: dict[int, int] = {}
    for a_id, b_id in matching:
        pairs[a_id] = b_id
        pairs[b_id] = a_id
    return pairs
