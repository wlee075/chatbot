import pytest
from graph.nodes import _evaluate_duplicate_candidate
from graph.state import PRDState

class MockSection:
    id = "test_section"

def test_duplicate_guard_flags_recent_question_match():
    state = PRDState(
        recent_questions=["What is the mapping logic?"],
        confirmed_qa_store={}
    )
    is_valid, reason, matched = _evaluate_duplicate_candidate(
        "What is the mapping logic?", ["review_workflow"], ["review_workflow"], "q123", state, MockSection(), {"thread_id": "test", "run_id": "test"}
    )
    assert not is_valid
    assert reason == "recent_question_match"
    assert "mapping logic" in matched

def test_duplicate_guard_flags_exact_text_match_in_canonical_store():
    state = PRDState(
        recent_questions=[],
        confirmed_qa_store={
            "mapping": {"section_id": "test_section", "questions": "What happens during mapping?"}
        }
    )
    is_valid, reason, matched = _evaluate_duplicate_candidate(
        "What happens during mapping?", ["success_metric"], ["success_metric"], "q124", state, MockSection(), {"thread_id": "test", "run_id": "test"}
    )
    assert not is_valid
    assert reason == "exact_text_match"

def test_duplicate_guard_flags_semantic_overlap():
    state = PRDState(
        recent_questions=[],
        confirmed_qa_store={
            "audience": {"section_id": "test_section", "questions": "different question here", "resolved_subparts": ["action_required"]}
        }
    )
    is_valid, reason, matched = _evaluate_duplicate_candidate(
        "Who is the target audience?", ["action_required"], ["action_required"], "q125", state, MockSection(), {"thread_id": "test", "run_id": "test"}
    )
    assert not is_valid
    assert reason == "semantic_match"

def test_duplicate_guard_allows_valid_candidate():
    state = PRDState(
        recent_questions=["What is the final goal?"],
        confirmed_qa_store={}
    )
    is_valid, reason, matched = _evaluate_duplicate_candidate(
        "What are the acceptance criteria?", ["acceptance_criteria"], ["acceptance_criteria"], "q126", state, MockSection(), {"thread_id": "test", "run_id": "test"}
    )
    assert is_valid
    assert reason == ""

