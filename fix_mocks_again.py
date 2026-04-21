import glob
import re

for filepath in glob.glob("tests/test_*.py"):
    with open(filepath, "r") as f:
        content = f.read()

    # Replace 5-element tuples: (A, B, C, None, None) -> (A, B, C, None)
    content = re.sub(
        r'return_value=\(([^,]+),\s*([^,]+),\s*([^,]+),\s*None,\s*None\)',
        r'return_value=(\1, \2, \3, None)',
        content
    )

    with open(filepath, "w") as f:
        f.write(content)
