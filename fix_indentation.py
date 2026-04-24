import glob
import re

for filepath in glob.glob("tests/test_*.py"):
    with open(filepath, "r") as f:
        content = f.read()

    # Capture whitespace before `from graph.routing import route_after_intent`
    # and apply it to `route_after_echo = route_after_intent`
    content = re.sub(
        r'([ \t]*)from graph\.routing import route_after_intent\nroute_after_echo = route_after_intent',
        r'\1from graph.routing import route_after_intent\n\1route_after_echo = route_after_intent',
        content
    )

    with open(filepath, "w") as f:
        f.write(content)
