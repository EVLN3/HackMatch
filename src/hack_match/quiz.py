"""Quiz questions and user profile data model."""
from dataclasses import dataclass, field
from enum import Enum


class ScoringRule(Enum):
    ALIGNED = "aligned"  # same answer scores higher
    OVERLAP = "overlap"  # multi-select; shared options score higher
    ORDINAL_CLOSENESS = "ordinal_closeness"  # answers close together on a scale score higher
    CROSS_MATCH = "cross_match"  # A's answer should appear in B's answer to `cross_field`
    CUSTOM_MATRIX = "custom_matrix"  # pairwise compatibility looked up from `matrix`


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    options: list[str]
    # None means "informational only" (not scored directly) e.g. Looking For
    # is scored via the `role` question's CROSS_MATCH, not on its own.
    scoring_rule: ScoringRule | None
    multi: bool = False
    max_select: int | None = None  # cap on selections when multi=True; None = select all
    free_text: bool = False  # answered via a text box (e.g. contact info), not buttons
    order: list[str] | None = None  # required for ORDINAL_CLOSENESS
    cross_field: str | None = None  # required for CROSS_MATCH
    matrix: dict[tuple[str, str], float] | None = None  # required for CUSTOM_MATRIX


def _symmetric_matrix(pairs: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    matrix = dict(pairs)
    for (x, y), score in pairs.items():
        matrix.setdefault((y, x), score)
    return matrix


# Leader+Builder complements well; Leader+Leader tends to clash; Flexible
# pairs reasonably with everyone. Tune these as real usage shows patterns.
TEAM_ROLE_MATRIX = _symmetric_matrix({
    ("Leader", "Builder"): 1.0,
    ("Leader", "Designer"): 0.8,
    ("Leader", "Presenter"): 0.7,
    ("Leader", "Flexible"): 0.9,
    ("Leader", "Leader"): 0.2,
    ("Builder", "Builder"): 0.6,
    ("Builder", "Designer"): 0.8,
    ("Builder", "Presenter"): 0.7,
    ("Builder", "Flexible"): 0.9,
    ("Designer", "Designer"): 0.5,
    ("Designer", "Presenter"): 0.7,
    ("Designer", "Flexible"): 0.9,
    ("Presenter", "Presenter"): 0.5,
    ("Presenter", "Flexible"): 0.9,
    ("Flexible", "Flexible"): 0.8,
})

# "Build my own" + "Join someone else's" is the ideal pairing; two people
# both wanting to lead their own idea (or both just waiting to join) is worse.
IDEA_OWNERSHIP_MATRIX = _symmetric_matrix({
    ("Build my own idea", "Join someone else's idea"): 1.0,
    ("Build my own idea", "Build my own idea"): 0.2,
    ("Build my own idea", "Either"): 0.8,
    ("Join someone else's idea", "Join someone else's idea"): 0.3,
    ("Join someone else's idea", "Either"): 0.8,
    ("Either", "Either"): 0.7,
})

# Mentor+mentee is the ideal pairing (both want the arrangement); two mentors
# or two mentees is a mismatch; independent pairs fine with anything.
MENTORING_MATRIX = _symmetric_matrix({
    ("Mentor someone less experienced", "Get guidance from someone more experienced"): 1.0,
    ("Mentor someone less experienced", "Mentor someone less experienced"): 0.3,
    ("Mentor someone less experienced", "Work independently"): 0.6,
    ("Get guidance from someone more experienced", "Get guidance from someone more experienced"): 0.3,
    ("Get guidance from someone more experienced", "Work independently"): 0.6,
    ("Work independently", "Work independently"): 0.8,
})

ROLE_OPTIONS = [
    "Frontend",
    "Backend",
    "Full Stack",
    "AI/ML",
    "Mobile",
    "Design/UI/UX",
    "Product/Business",
    "Hardware Designer",
]

QUESTIONS: list[Question] = [
    Question(
        id="role",
        text="Which roles does your team already have?",
        options=ROLE_OPTIONS,
        scoring_rule=ScoringRule.CROSS_MATCH,
        cross_field="looking_for",
        multi=True,
    ),
    Question(
        id="looking_for",
        text="Which roles are you looking for on your team?",
        options=ROLE_OPTIONS,
        scoring_rule=None,
        multi=True,
    ),
    Question(
        id="has_idea",
        text="Do you already have a project idea?",
        options=["Yes", "No"],
        scoring_rule=None,
    ),
    Question(
        id="categories",
        text="Which project categories interest you most? (select up to 3)",
        options=[
            "AI", "Fintech", "Healthcare", "Education", "Sustainability",
            "Gaming", "Productivity", "Cybersecurity", "Social Impact",
        ],
        scoring_rule=ScoringRule.OVERLAP,
        multi=True,
        max_select=3,
    ),
    Question(
        id="mentoring",
        text="What experience dynamic are you looking for?",
        options=[
            "Mentor someone less experienced",
            "Get guidance from someone more experienced",
            "Work independently",
        ],
        scoring_rule=ScoringRule.CUSTOM_MATRIX,
        matrix=MENTORING_MATRIX,
    ),
    Question(
        id="technologies",
        text="Which technologies would you like to work with?",
        options=[
            "Python", "JavaScript", "TypeScript", "React", "Next.js",
            "Node.js", "Java", "C++", "Flutter", "React Native", "AWS",
            "Azure", "CAD", "Other",
        ],
        scoring_rule=ScoringRule.OVERLAP,
        multi=True,
    ),
    Question(
        id="team_role",
        text="Which role do you naturally take in a team?",
        options=["Leader", "Builder", "Designer", "Presenter", "Flexible"],
        scoring_rule=ScoringRule.CUSTOM_MATRIX,
        matrix=TEAM_ROLE_MATRIX,
    ),
    Question(
        id="availability",
        text="How committed are you during the hackathon?",
        options=["Casual", "Moderate", "All-in"],
        scoring_rule=ScoringRule.ALIGNED,
    ),
    Question(
        id="idea_ownership",
        text="Would you rather...",
        options=["Build my own idea", "Join someone else's idea", "Either"],
        scoring_rule=ScoringRule.CUSTOM_MATRIX,
        matrix=IDEA_OWNERSHIP_MATRIX,
    ),
    Question(
        id="priority",
        text="What's more important to you?",
        options=["Winning", "Learning", "Meeting people", "Building something cool"],
        scoring_rule=ScoringRule.ALIGNED,
    ),
    Question(
        id="contact",
        text="How can a match reach you? (GitHub, LinkedIn, Discord tag, Instagram, etc.)",
        options=[],
        scoring_rule=None,
        free_text=True,
    ),
]

QUESTIONS_BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}


@dataclass
class Profile:
    user_id: int
    answers: dict[str, str | list[str]] = field(default_factory=dict)
