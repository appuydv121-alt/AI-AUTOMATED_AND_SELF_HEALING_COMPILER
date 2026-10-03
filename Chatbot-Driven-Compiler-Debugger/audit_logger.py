import json
import os
from datetime import datetime

# The file where our multi-turn chats will be saved
LOG_FILE = "system_interactions.json"

def log_interaction(user_prompt, llm_response, code_snippet="N/A", error_category="N/A", fixed_code="N/A"):
    """
    Appends a single interaction turn to the JSON log file.
    Updated for Phase 6 to include the AI's generated fixed_code.
    """
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error_category": error_category,
        "code_snippet": code_snippet,
        "user_prompt": user_prompt,
        "llm_response": llm_response,
        "fixed_code": fixed_code  
    }

    # 1. Read existing data if the file already exists
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                data = [] # If file is corrupted or empty, start fresh
    else:
        data = []

    # 2. Append the new conversation turn
    data.append(log_entry)

    # 3. Write it back to the JSON file (indent=4)
    with open(LOG_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)