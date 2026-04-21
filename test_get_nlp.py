import spacy
from spacy.tokens import Span, Token
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
from graph.nodes import _get_semantic_cues
nlp = spacy.load("en_core_web_sm")
doc = nlp("We used to use SAP.")
print("ENTS:", [(ent.text, _get_semantic_cues(ent)) for ent in doc.ents], flush=True)
