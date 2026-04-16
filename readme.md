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

## 🛑 Stopping the App

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
