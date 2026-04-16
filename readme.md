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
│   ├── __pycache__
│   │   ├── __init__.cpython-313.pyc
│   │   └── sections.cpython-313.pyc
│   └── sections.py
├── dummy_context.txt
├── flow_test.csv
├── graph
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-313.pyc
│   │   ├── builder.cpython-313.pyc
│   │   ├── nodes.cpython-313.pyc
│   │   ├── routing.cpython-313.pyc
│   │   └── state.cpython-313.pyc
│   ├── builder.py
│   ├── nodes.py
│   ├── routing.py
│   └── state.py
├── prompts
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-313.pyc
│   │   └── templates.cpython-313.pyc
│   └── templates.py
├── readme.md
├── reflector_eval_runs.csv
├── reflector_eval_summary.csv
├── requirements.txt
├── tests
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-313.pyc
│   │   ├── ci_checks.cpython-313.pyc
│   │   ├── eval_cases.cpython-313.pyc
│   │   ├── fixtures.cpython-313.pyc
│   │   ├── run_reflector_eval.cpython-313.pyc
│   │   └── run_scoring_tests.cpython-313.pyc
│   ├── ci_checks.py
│   ├── eval_cases.py
│   ├── fixtures.py
│   ├── run_reflector_eval.py
│   └── run_scoring_tests.py
└── utils
    ├── __init__.py
    ├── __pycache__
    │   ├── __init__.cpython-313.pyc
    │   └── doc_parser.cpython-313.pyc
    └── doc_parser.py

11 directories, 41 files
```
