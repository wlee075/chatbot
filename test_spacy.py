import spacy
nlp = spacy.load("en_core_web_sm")
doc = nlp("We used to use SAP.")
for token in doc:
    print(token.text, token.dep_, token.head.text, token.head.pos_, token.head.tag_, [c.text for c in token.head.children])
doc = nlp("For example, if a user uploads a PDF...")
for token in doc:
    print(token.text, token.dep_, token.head.text, token.head.pos_, token.head.tag_, [c.text for c in token.head.children])
