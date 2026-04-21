import glob
import re

for filepath in glob.glob("tests/test_*.py"):
    with open(filepath, "r") as f:
        content = f.read()

    # 1. Fix the `intent, _, _, _, _, _ = _classify_intent_rule(...)` back to 4 elements
    content = re.sub(r'intent,\s*_,\s*_,\s*_,\s*_,\s*_\s*=\s*_classify_intent_rule', r'intent, _, _, _ = _classify_intent_rule', content)
    
    # 2. Fix `intent, _, source, _, _, _` back to `intent, _, source, _`
    content = re.sub(r'intent,\s*_,\s*source,\s*_,\s*_,\s*_\s*=\s*_classify_intent_rule', r'intent, _, source, _ = _classify_intent_rule', content)

    # 3. Fix `i1, a1, s1, _, _, _` back to `i1, a1, s1, _`
    content = content.replace("i1, a1, s1, _, _, _ = _classify_intent_rule", "i1, a1, s1, _ = _classify_intent_rule")
    content = content.replace("i2, a2, s2, _, _, _ = _classify_intent_rule", "i2, a2, s2, _ = _classify_intent_rule")

    # 4. Fix any other remaining 6 or 5 unpacks
    content = re.sub(r'([a-zA-Z0-9_]+),\s*_\s*,\s*_\s*,\s*_\s*,\s*_\s*,\s*_\s*=\s*_classify_intent_rule', r'\1, _, _, _ = _classify_intent_rule', content)
    content = re.sub(r'([a-zA-Z0-9_]+),\s*_\s*,\s*_\s*,\s*_\s*,\s*_\s*=\s*_classify_intent_rule', r'\1, _, _, _ = _classify_intent_rule', content)
    
    # Also 7-unpacks from earlier `intent, _, _, _, _, _, _ = `
    content = re.sub(r'intent,\s*_,\s*_,\s*_,\s*_,\s*_,\s*_\s*=\s*_classify_intent_rule', r'intent, _, _, _ = _classify_intent_rule', content)

    # 5. Fix `intent, extracted, _, _, ...`
    content = re.sub(r'intent,\s*extracted,\s*_\s*,\s*_\s*,\s*_\s*,\s*_\s*=\s*_classify_intent_rule', r'intent, extracted, _, _ = _classify_intent_rule', content)

    # 6. Replace old route_after_echo imports
    content = content.replace("from graph.routing import route_after_echo", "from graph.routing import route_after_intent\nroute_after_echo = route_after_intent")
    
    # 7. Also catch the bad mock value `return_value=("AMBIGUOUS", None, "llm_fallback", None, None))`
    content = content.replace('return_value=("AMBIGUOUS", None, "llm_fallback", None, None))', 'return_value=("AMBIGUOUS", None, "llm_fallback", None))')

    # Fix test_llm_fallback_adjudicator.py where it might have `test_rule_based_filter_runs_before_llm_fallback - ValueError: not enough values to unpack (expected 4, got 3)`
    # This means the mock return values in test_llm_fallback_adjudicator.py were 3 elements instead of 4.
    content = re.sub(r'return_value=\(([^,]+),\s*([^,]+),\s*([^\)]+)\)', r'return_value=(\1, \2, \3, None)', content)

    with open(filepath, "w") as f:
        f.write(content)
