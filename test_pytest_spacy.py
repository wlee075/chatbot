import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
from graph.nodes import _log_keyword_extraction_observability
res = _log_keyword_extraction_observability("We used to use SAP.", "msg_id")
for c in res["candidates"]:
    print(c["surface"], c["normalized"], c["is_historical"])
