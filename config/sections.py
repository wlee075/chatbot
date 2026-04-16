from dataclasses import dataclass
from typing import List


@dataclass
class PRDSection:
    id: str
    title: str
    description: str
    expected_components: List[str]
    specificity_guidance: str
    allow_high_level: bool = False


PRD_SECTIONS: List[PRDSection] = [
    PRDSection(
        id="tldr",
        title="tl;dr",
        description="A concise one-paragraph summary of the entire initiative — problem, solution, and expected impact.",
        expected_components=[
            "what problem is being solved",
            "who is affected",
            "what the solution is",
            "expected impact or outcome",
        ],
        specificity_guidance="This section is intentionally high-level. A 2–3 sentence summary is appropriate.",
        allow_high_level=True,
    ),
    PRDSection(
        id="elevator_pitch",
        title="Elevator Pitch",
        description="A compelling 30-second pitch for executive stakeholders that captures the initiative's value proposition.",
        expected_components=[
            "target user or persona",
            "unmet need or pain point",
            "proposed solution",
            "key benefit or differentiator",
        ],
        specificity_guidance="Should be specific enough to distinguish this initiative from alternatives. Avoid generic phrases like 'better experience'.",
        allow_high_level=True,
    ),
    PRDSection(
        id="key_stakeholders",
        title="Key Stakeholders",
        description="Identifies who is involved, their roles, and any sign-off requirements.",
        expected_components=[
            "stakeholder names or roles listed",
            "type of involvement (Responsible / Accountable / Consulted / Informed)",
            "any formal sign-off or approval requirements",
        ],
        specificity_guidance="Roles should be specific (e.g. 'Trust & Safety Engineering Lead') not vague (e.g. 'the team').",
    ),
    PRDSection(
        id="background",
        title="Background",
        description="Context and history that motivated this initiative — what triggered it and what has been tried before.",
        expected_components=[
            "description of the current state or pain point",
            "how the problem was identified (data, incidents, or user feedback)",
            "any prior attempts or related work and why they were insufficient",
        ],
        specificity_guidance="Should reference specific incidents, metrics, or observations — not generic statements like 'the current system is slow'.",
    ),
    PRDSection(
        id="problem_statement",
        title="Problem Statement",
        description="A precise articulation of the problem being solved, without jumping to solutions.",
        expected_components=[
            "who experiences the problem",
            "what the problem specifically is",
            "why it matters (business or user impact)",
            "what happens if it is not solved",
        ],
        specificity_guidance="Avoid solution language. Focus only on the problem. Quantify impact where possible.",
    ),
    PRDSection(
        id="goals",
        title="Goals",
        description="What this initiative aims to achieve — the desired outcomes.",
        expected_components=[
            "each goal is distinct and non-overlapping",
            "goals are measurable or time-bound",
            "goals directly address the problem statement",
        ],
        specificity_guidance="Avoid vague goals like 'improve performance'. Each goal should include a target (e.g. 'reduce false positive rate by 20% by Q3 2026').",
    ),
    PRDSection(
        id="success_metrics",
        title="Success Metrics",
        description="Quantifiable indicators that will determine whether the initiative succeeded.",
        expected_components=[
            "metric name",
            "baseline value (current state)",
            "target value (definition of success)",
            "measurement method or data source",
            "evaluation timeline",
        ],
        specificity_guidance="Each metric must be quantifiable. 'User satisfaction' is not a metric. 'NPS score ≥ 40 measured via quarterly survey by Q4 2026' is.",
    ),
    PRDSection(
        id="non_goals",
        title="Non-goals",
        description="What this initiative explicitly will NOT address — to prevent scope creep.",
        expected_components=[
            "at least one explicit non-goal stated",
            "non-goals do not contradict the stated goals",
            "reason why each item is out of scope",
        ],
        specificity_guidance="Non-goals should be specific enough to prevent ambiguity (e.g. 'We will not support manual review workflows in this phase').",
    ),
    PRDSection(
        id="assumptions",
        title="Assumptions & Validations",
        description="Key assumptions the solution depends on, with a validation plan for each.",
        expected_components=[
            "assumption clearly stated",
            "risk if the assumption proves false",
            "how and when the assumption will be validated",
        ],
        specificity_guidance="Assumptions without validation plans are just risks. Each assumption needs a concrete validation method and owner.",
    ),
    PRDSection(
        id="out_of_scope",
        title="Out of Scope",
        description="Work, features, or use cases explicitly excluded from this initiative.",
        expected_components=[
            "specific items listed as out of scope",
            "reason for exclusion",
            "no contradiction with the proposed solution or features",
        ],
        specificity_guidance="Items should be specific. 'Mobile support' is more useful than 'some platforms'. Cross-reference with Non-goals for consistency.",
    ),
    PRDSection(
        id="proposed_solution",
        title="Proposed Solution & Features",
        description="The solution being built: approach, key features, user flows, and technical context.",
        expected_components=[
            "solution approach described at a high level",
            "key features or capabilities listed",
            "user flows or design references included",
            "technical dependencies or integrations identified",
        ],
        specificity_guidance="Features should be specific enough for engineering to scope. Avoid 'smart' or 'intelligent' without explaining the mechanism.",
    ),
    PRDSection(
        id="risks",
        title="Risks",
        description="Risks that could impact delivery, adoption, or success of the initiative.",
        expected_components=[
            "risk description",
            "likelihood assessment (High / Medium / Low)",
            "impact assessment (High / Medium / Low)",
            "mitigation plan for each risk",
        ],
        specificity_guidance="Every risk must have a mitigation plan, not just identification. 'Monitor the situation' is not a mitigation.",
    ),
    PRDSection(
        id="timeline",
        title="Timeline",
        description="Key milestones, owners, and delivery dates.",
        expected_components=[
            "milestones listed",
            "owner assigned to each milestone",
            "specific dates or sprint references",
        ],
        specificity_guidance="Dates must be specific (e.g. 'Q3 2026 Week 2') not vague ('soon' or 'next quarter'). Include dependencies between milestones if applicable.",
    ),
]

SECTION_IDS = [s.id for s in PRD_SECTIONS]


def get_section_by_index(index: int) -> PRDSection:
    return PRD_SECTIONS[index]


def get_section_by_id(section_id: str) -> PRDSection:
    return next(s for s in PRD_SECTIONS if s.id == section_id)
