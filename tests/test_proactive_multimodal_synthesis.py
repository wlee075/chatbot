import pytest
from unittest.mock import patch, MagicMock
from graph.state import PRDState
from graph.nodes import generate_questions_node, discovery_questions_node
import graph.nodes

@pytest.fixture
def mock_llm_invoke():
    # We patch llm_invoke and _get_llm to prevent real API key validation constraints.
    with patch('graph.nodes._get_llm') as mock_llm, \
         patch('graph.nodes.llm_invoke') as mock_invoke:
        mock_llm.return_value = MagicMock()
        mock_invoke.return_value = MagicMock(content="Mocked LLM Question?")
        yield mock_invoke

def test_first_response_after_text_plus_image_attempts_connection(mock_llm_invoke):
    """
    Ensure generate_questions_node injects the proactive synthesis rule when bg_contexts exist.
    """
    state = PRDState({
        "section_index": 0,
        "prd_sections": {},
        "background_generated_contexts": [
            {
                "context_id": "123",
                "is_active": True,
                "created_at": "2026-04-20T10:00:00Z",
                "edited_summary": "A sketch of a user profile screen."
            }
        ]
    })
    
    # Mock section lookup
    with patch('graph.nodes.get_section_by_index') as mock_sec, \
         patch('graph.nodes.build_conversation_understanding_output') as mock_br:
        mock_sec.return_value = MagicMock(title="UI", id="ui", expected_components=["Mock Component"])
        mock_br.return_value = {}
        
        generate_questions_node(state)
        
        # Verify the prompt string sent to llm_invoke
        assert mock_llm_invoke.called
        call_args = mock_llm_invoke.call_args[0]
        messages = call_args[1]
        prompt_content = messages[0].content
        
        assert "VERIFIED VISUAL CONTEXT" in prompt_content
        assert "A sketch of a user profile screen" in prompt_content
        # PROACTIVE CONNECTION check
        assert "Actively connect the image to their text goal immediately." in prompt_content

def test_low_confidence_image_text_connection_uses_bounded_language(mock_llm_invoke):
    """
    Ensure bounded inference ('tentative language') rules are embedded in the visual prompt instructions.
    """
    state = PRDState({
        "section_index": 0,
        "prd_sections": {},
        "background_generated_contexts": [
            {
                "context_id": "123",
                "is_active": True,
                "created_at": "2026-04-20T10:00:00Z",
                "generated_summary": "Appears to be some sort of UI, but highly blurry."
            }
        ]
    })
    
    with patch('graph.nodes.get_section_by_index') as mock_sec, \
         patch('graph.nodes.build_conversation_understanding_output') as mock_br:
        mock_sec.return_value = MagicMock(title="UI", id="ui", expected_components=["Mock Component"])
        mock_br.return_value = {}
        
        generate_questions_node(state)
        
        call_args = mock_llm_invoke.call_args[0]
        prompt_content = call_args[1][0].content
        
        assert "Bounded Inference: Do not over-commit" in prompt_content
        assert "Assuming this relates to" in prompt_content

def test_text_only_path_does_not_mention_visual_context(mock_llm_invoke):
    """
    Ensure the prompt remains strictly text-only if no active bg_contexts exist.
    """
    state = PRDState({
        "section_index": 0,
        "prd_sections": {},
        "background_generated_contexts": [] # Empty!
    })
    
    with patch('graph.nodes.get_section_by_index') as mock_sec, \
         patch('graph.nodes.build_conversation_understanding_output') as mock_br:
        mock_sec.return_value = MagicMock(title="UI", id="ui", expected_components=["Mock Component"])
        mock_br.return_value = {}
        
        generate_questions_node(state)
        
        call_args = mock_llm_invoke.call_args[0]
        prompt_content = call_args[1][0].content
        
        assert "VERIFIED VISUAL CONTEXT" not in prompt_content
        assert "proactively synthesize this visual context" not in prompt_content

def test_first_turn_discovery_now_sees_image_context(mock_llm_invoke):
    """
    Ensure discovery_questions_node, which previously dropped multimodal contexts, now actively injects them.
    """
    state = PRDState({
        "framing_mode": "confused",
        "discovery_turn_count": 0,
        "background_generated_contexts": [
            {
                "context_id": "123",
                "is_active": True,
                "created_at": "2026-04-20T10:00:00Z",
                "edited_summary": "A complex workflow diagram."
            }
        ]
    })
    
    with patch('graph.nodes.build_conversation_understanding_output') as mock_br:
        mock_br.return_value = {}
        
        discovery_questions_node(state)
        
        call_args = mock_llm_invoke.call_args[0]
        prompt_content = call_args[1][0].content
        
        assert "VERIFIED VISUAL CONTEXT" in prompt_content
        assert "A complex workflow diagram" in prompt_content
        assert "Actively connect the image" in prompt_content
