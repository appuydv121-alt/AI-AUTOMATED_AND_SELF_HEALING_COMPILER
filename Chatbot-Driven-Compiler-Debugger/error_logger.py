import csv
import re
import os
from datetime import datetime

# File to store our dataset
LOG_FILE = "compiler_errors.csv"

def initialize_log_file():
    """Creates the CSV file with headers if it doesn't exist."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "Error_Type", "Line_Number", "Raw_Message", "Source_Code_Snippet"])

def parse_gcc_error(raw_error_text):
    """
    Parses messy GCC output into structured data.
    Regex logic: Looks for pattern 'file:line:column: error: message'
    """
    # Regex to find: line number, column, and the actual message
    # GCC Error : temp_code.cpp:4:5: error: expected ';' before 'return'
    pattern = r"temp_code\.cpp:(\d+):(\d+):\s+(error|warning):\s+(.*)"
    
    match = re.search(pattern, raw_error_text)
    if match:
        line_num = match.group(1)
        severity = match.group(3) # "error" or "warning"
        message = match.group(4)
        return line_num, severity, message
    
    return "Unknown", "Error", raw_error_text # Return if parsing fails

def log_error(raw_error_text, user_code):
    """
    Main function to save the error to our dataset.
    """
    initialize_log_file()
    
    # 1. Parse the error
    line_num, severity, clean_message = parse_gcc_error(raw_error_text)
    
    # 2. Get the specific line of code that failed (for context)
    code_lines = user_code.split('\n')
    try:
        # adjusting for 0-based index vs 1-based line number
        failed_line_index = int(line_num) - 1
        source_snippet = code_lines[failed_line_index].strip() if 0 <= failed_line_index < len(code_lines) else "N/A"
    except:
        source_snippet = "N/A"

    # 3. Save to CSV
    with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([timestamp, clean_message, line_num, raw_error_text, source_snippet])
        
    return clean_message # Return to UI