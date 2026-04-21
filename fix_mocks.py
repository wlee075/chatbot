import glob
import re

for filepath in glob.glob("tests/test_*.py"):
    with open(filepath, "r") as f:
        content = f.read()

    # Fix return_values that have exactly 3 tuple elements: (A, B, C)
    content = re.sub(r'return_value=\(([^,]+),\s*([^,]+),\s*([^\)]+)\)', r'return_value=(\1, \2, \3, None)', content)

    # Fix unpacking intent, _, _ = ... to intent, _, _, _ = ...
    content = re.sub(r'([a-zA-Z0-9_]+),\s*_,\s*_\s*=\s*_classify_intent_rule', r'\1, _, _, _ = _classify_intent_rule', content)
    
    # Fix unpacking intent, _ = ... to intent, _, _, _ = ... (careful about intent, extracted =)
    content = re.sub(r'([a-zA-Z0-9_]+),\s*_\s*=\s*_classify_intent_rule', r'\1, _, _, _ = _classify_intent_rule', content)
    
    # Replace intent, extracted = _classify_intent_rule
    content = re.sub(r'intent,\s*extracted\s*=\s*_classify_intent_rule', r'intent, extracted, _, _ = _classify_intent_rule', content)

    with open(filepath, "w") as f:
        f.write(content)

