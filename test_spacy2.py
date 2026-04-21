import spacy
from spacy.tokens import Span, Token
nlp = spacy.load("en_core_web_sm")
doc = nlp("We used to use SAP.")
for chunk in doc.noun_chunks:
    root = getattr(chunk, "root", chunk)
    text = chunk.sent.text.lower() if hasattr(chunk, "sent") else getattr(chunk.doc, "text", "").lower()
    print("CHUNK", chunk.text, text, any(w in text for w in ("used to",)))
for token in doc:
    root = getattr(token, "root", token)
    text = token.sent.text.lower() if hasattr(token, "sent") else getattr(token.doc, "text", "").lower()
    print("TOKEN", token.text, text, any(w in text for w in ("used to",)))
