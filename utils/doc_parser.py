import io


def parse_uploaded_file(uploaded_file) -> str:
    """
    Parse a Streamlit UploadedFile into plain text.
    Supports: .pdf, .txt, .md
    """
    if uploaded_file is None:
        return ""

    name = uploaded_file.name.lower()

    if name.endswith(".pdf"):
        return _parse_pdf(uploaded_file)

    if name.endswith((".txt", ".md")):
        raw = uploaded_file.read()
        return raw.decode("utf-8", errors="replace").strip()

    return ""


def _parse_pdf(uploaded_file) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(uploaded_file.read()))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()

    except ImportError:
        return "[PDF parsing requires pypdf. Run: pip install pypdf]"

    except Exception as exc:
        return f"[Could not parse PDF: {exc}]"
