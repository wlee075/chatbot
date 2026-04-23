import pytest
from graph.nodes import _apply_transition_cleanup

def test_standalone_transition_string_is_not_stripped():
    qo = {"subparts": ["random_subpart"]}
    # Passing the pre-existing transition string to ensure the cleanup logic preserves it
    current_q_obj, final_questions = _apply_transition_cleanup(
        "I have all the details I need for this section. Let's move on.", 
        qo
    )
    
    assert final_questions == "I have all the details I need for this section. Let's move on."
    assert current_q_obj["subparts"] == []

def test_transition_prefix_is_stripped_only_when_followed_by_real_question_text():
    qo = {"subparts": ["action_required"]}
    concat_question = "I have all the details I need for this section. Let's move on. Wait, what is the action required?"
    
    current_q_obj, final_questions = _apply_transition_cleanup(
        concat_question, 
        qo
    )
    
    assert final_questions == "Wait, what is the action required?"
    assert current_q_obj["subparts"] == ["action_required"]

def test_duplicate_guard_transition_does_not_produce_blank_output():
    # If the subparts are entirely empty, and the duplicate guard fires, it must output exactly the transition string
    qo = {"subparts": []}
    
    current_q_obj, final_questions = _apply_transition_cleanup(
        "I have all the details I need for this section. Let's move on.", 
        qo
    )
    
    # Must NOT be an empty string!
    assert final_questions == "I have all the details I need for this section. Let's move on."
