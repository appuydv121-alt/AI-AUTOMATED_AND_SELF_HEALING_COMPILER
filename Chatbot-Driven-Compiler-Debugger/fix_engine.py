import re
from secure_scan import run_security_guardrail

def validate_ai_fix(llm_response):
    """
    Extracts the C++ code block from the LLM's response and scans it for vulnerabilities.
    Returns: (is_safe, extracted_code, security_warning)
    """
    # 1. Extract the code block using regex
    # Looks for text between ```cpp and ```
    match = re.search(r'```cpp\n(.*?)\n```', llm_response, re.DOTALL)
    
    if not match:
        return False, None, "No valid C++ code block was found in the AI suggestion."
    
    extracted_code = match.group(1).strip()
    
    # 2. Validate the AI-generated code for secure coding practices
    
    is_safe, warning_msg = run_security_guardrail(extracted_code)
    
    if not is_safe:
        return False, extracted_code, f"The AI suggested an insecure function: {warning_msg}"
        
    return True, extracted_code, "The suggested fix is secure."