import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
from graph.nodes import _get_semantic_cues
import spacy
nlp = spacy.load("en_core_web_sm")
doc = nlp("We used to use SAP.")
for chunk in doc.noun_chunks:
    print(chunk.text, _get_semantic_cues(chunk))
for token in doc:
    print(token.text, _get_semantic_cues(token))
