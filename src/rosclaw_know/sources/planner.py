"""Bounded deterministic multi-perspective research planner."""

from __future__ import annotations

from pydantic import Field

from rosclaw_know.contracts import ResearchRequestV2
from rosclaw_know.contracts.base import StrictContract

DEFAULT_PERSPECTIVES = (
    "existing_projects",
    "mechanism",
    "implementation",
    "compatibility",
    "failure_modes",
    "deployment",
)


class ResearchSubquestion(StrictContract):
    perspective: str
    question: str
    search_queries: list[str] = Field(min_length=1, max_length=4)


class ResearchPlan(StrictContract):
    request_id: str
    perspectives: list[str] = Field(min_length=1, max_length=12)
    subquestions: list[ResearchSubquestion] = Field(min_length=1, max_length=40)
    source_preferences: list[str]
    expected_outputs: list[str]
    stop_conditions: list[str]
    max_sources: int
    token_budget: int


_QUESTION_TEMPLATES = {
    "existing_projects": "Which reusable primary projects already address {topic}?",
    "mechanism": "Which mechanisms and assumptions make approaches to {topic} work?",
    "implementation": "Which exact modules, configs and entrypoints implement {topic}?",
    "compatibility": "Which robot, simulator, ROS and version constraints limit {topic}?",
    "failure_modes": "Which resolved issues, regressions and limitations affect {topic}?",
    "deployment": "How do projects validate, deploy and transfer {topic} to hardware?",
}


def build_research_plan(request: ResearchRequestV2) -> ResearchPlan:
    perspectives = request.perspectives or list(DEFAULT_PERSPECTIVES)
    perspectives = list(dict.fromkeys(perspectives))[:12]
    subquestions = []
    for perspective in perspectives:
        template = _QUESTION_TEMPLATES.get(
            perspective, "What primary evidence explains {perspective} for {topic}?"
        )
        question = template.format(topic=request.topic, perspective=perspective.replace("_", " "))
        constraints = " ".join(
            value
            for value in (
                request.constraints.robot_model,
                request.constraints.simulator,
                request.constraints.ros_distro,
            )
            if value
        )
        subquestions.append(
            ResearchSubquestion(
                perspective=perspective,
                question=question,
                search_queries=list(
                    dict.fromkeys(
                        filter(
                            None,
                            (
                                f"{request.topic} {perspective.replace('_', ' ')}",
                                f"{request.topic} {constraints}".strip(),
                            ),
                        )
                    )
                )[:4],
            )
        )
    return ResearchPlan(
        request_id=request.request_id,
        perspectives=perspectives,
        subquestions=subquestions,
        source_preferences=[
            "official_documentation",
            "repository",
            "paper",
            "issue",
            "pull_request",
            "web",
        ],
        expected_outputs=["project_card", "project_wiki", "knowledge_units", "reference_pack"],
        stop_conditions=[
            f"qualified_sources>={min(request.max_sources, 8)}",
            f"source_limit={request.max_sources}",
            f"token_budget={request.token_budget}",
            "all_claims_have_pinned_evidence",
        ],
        max_sources=request.max_sources,
        token_budget=request.token_budget,
    )
