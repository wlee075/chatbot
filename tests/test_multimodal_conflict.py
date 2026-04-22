from graph.state import PRDState
from graph.split_nodes import truth_commit_node
from graph.nodes import generate_questions_node

def test_image_text_conflict_blocks_truth_commit():
    state = PRDState({
        "materialization_conflict": True,
        "is_eligible": True,
    })
    res = truth_commit_node(state)
    assert not any("store_update" in k for k in res), "Truth commit must not commit when conflicting"
    assert "chat_history" in res, "Must return system interjection"
    assert "image and text seem different" in res["chat_history"][0]["content"]

def test_image_text_conflict_triggers_clarification_question():
    state = PRDState({
        "materialization_conflict": True,
        "materialization_conflict_reason": "they say different things",
        "phase": "elicitation"
    })
    res = generate_questions_node(state)
    assert "seem to suggest different things" in res["current_questions"]
    assert not res["materialization_conflict"]

def test_non_conflicting_image_text_turn_still_commits_normally():
    from graph.nodes import PRD_SECTIONS
    state = PRDState({
        "materialization_conflict": False,
        "is_eligible": True,
        "raw_answer_buffer": "some text",
        "effective_answer_for_commit": "combined text",
        "section_index": 0,
        "current_questions": "Q1"
    })
    res = truth_commit_node(state)
    assert "chat_history" not in res, "Should not block truth commit for normal flow"

def test_bounded_language_used_for_image_text_conflict_question():
    state = PRDState({
        "materialization_conflict": True,
        "materialization_conflict_reason": "testing"
    })
    res = generate_questions_node(state)
    assert "Could you clarify which one is correct?" in res["current_questions"]
