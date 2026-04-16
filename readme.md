# Chatbot Setup & Run Guide

## How to Run the Chatbot

### 1. Activate the Virtual Environment
Make sure your virtual environment is activated before running the app.

```bash
source .venv/bin/activate
```

---

### 2. Configure Environment Variables
Ensure your `.env` file contains your API key.

Example:
```env
API_KEY=your_api_key_here
```

---

### 3. (Optional) Override the Model
If needed, update the model configuration in your `.env` or config file.

---

### 4. Start the Application

```bash
streamlit run app.py
```

Streamlit will generate a local URL (typically):

```
http://localhost:8501
```

Open this in your browser to use the chatbot.

---

## Stopping the App

To stop the chatbot, press:

```bash
Ctrl + C
```

in the terminal.

---

## Notes
- Ensure all dependencies are installed:
  ```bash
  pip install -r requirements.txt
  ```
- Use Python 3.10+ for compatibility

## Project structure
```
.
├── app.py
├── config
│   ├── __init__.py
│   └── sections.py
├── dummy_context.txt
├── graph
│   ├── __init__.py
│   ├── builder.py
│   ├── nodes.py
│   ├── routing.py
│   └── state.py
├── prompts
│   ├── __init__.py
│   └── templates.py
├── readme.md
├── requirements.txt
├── tests
│   ├── __init__.py
│   ├── ci_checks.py
│   ├── eval_cases.py
│   ├── fixtures.py
│   ├── run_reflector_eval.py
│   └── run_scoring_tests.py
└── utils
    ├── __init__.py
    └── doc_parser.py

11 directories, 41 files
```
