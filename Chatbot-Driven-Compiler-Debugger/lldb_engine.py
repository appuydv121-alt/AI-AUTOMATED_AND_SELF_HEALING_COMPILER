import subprocess
import requests
import json
import os

# LLM config (Must match your app.py)
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3"

def execute_lldb_command(executable_path, command):
    """
    Runs a specific LLDB command on the compiled binary in batch mode.
    The '-b' flag is crucial: it prevents LLDB from opening an interactive 
    terminal and freezing our Streamlit app.
    """
    try:
        # lldb -b -o "run" -o "command" ./binary
        result = subprocess.run(
            ["lldb", "-b", "-o", "run", "-o", command, executable_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return "LLDB Timeout: The command took too long to execute."
    except Exception as e:
        return f"LLDB System Error: {str(e)}"

def agentic_debug_loop(cpp_code, crash_output, executable_path="./temp_program"):
    """
    The ReAct (Reason & Act) Loop.
    Allows Llama 3 to autonomously run LLDB commands to inspect memory.
    """
    
    print("\n[SYSTEM] 🤖 Initiating Agentic LLDB Loop...")
    
    # 1. The Strict Agentic System Prompt
    agent_prompt = f"""You are an Autonomous C++ Debugger running on Apple Silicon.
A C++ program just crashed with a Runtime Error (e.g., Segmentation Fault).

--- BUGGY CODE ---
{cpp_code}

--- CRASH OUTPUT ---
{crash_output}

You have access to the Apple LLDB Debugger. 
You must choose ONE of the following actions:
ACTION 1: Run an LLDB command to inspect memory (e.g., 'bt' for backtrace, or 'frame variable' to see local variables).
ACTION 2: Explain the error and provide the fix.

You MUST respond in strict JSON format:
{{
    "thought": "Your reasoning about what to do next",
    "action": "lldb_command" OR "final_fix",
    "command": "the exact lldb command to run (leave empty if final_fix)",
    "explanation": "Your final explanation to the user. You MUST include the fully corrected C++ code inside a markdown block. (leave empty if lldb_command)"
}}"""

    
# We will allow the AI a maximum of 2 "turns" to prevent an infinite loop
    max_turns = 2
    current_context = agent_prompt
    
    # NEW: We will store the AI's thought process here to show the UI
    ui_debug_log = "" 

    for turn in range(max_turns):
        print(f"[SYSTEM] Turn {turn + 1}: Asking Llama 3 for its next move...")
        
        payload = {"model": MODEL_NAME, "prompt": current_context, "stream": False, "format": "json"}
        response = requests.post(OLLAMA_URL, json=payload).json()['response']
        
        try:
            ai_decision = json.loads(response)
            thought = ai_decision.get('thought')
            action = ai_decision.get('action')
            
            print(f"   [LLM THOUGHT]: {thought}")
            # Add to our UI log
            ui_debug_log += f"**Turn {turn + 1} - 🧠 Thought:** {thought}\n\n"
            
            if action == "lldb_command":
                cmd = ai_decision.get("command")
                print(f"   [LLM ACTION]: Running LLDB command -> `{cmd}`")
                
                # Add command to UI log
                ui_debug_log += f"**🛠️ Action:** Ran LLDB command `{cmd}`\n\n"
                
                lldb_result = execute_lldb_command(executable_path, cmd)
                
                # Add Mac's response to UI log
                ui_debug_log += f"**💻 LLDB Response:**\n```text\n{lldb_result}\n```\n\n---\n\n"
                
                current_context += f"\n\n--- LLDB RESULT FOR '{cmd}' ---\n{lldb_result}\n\nNow, provide the 'final_fix' JSON based on this new memory data."
            
            elif action == "final_fix":
                print("   [LLM ACTION]: Delivering final fix.")
                ui_debug_log += "**✅ Action:** Diagnosis Complete. Delivering final fix."
                
                # We now return BOTH the explanation and the log
                return ai_decision.get("explanation"), ui_debug_log 
                
        except json.JSONDecodeError:
            return "Critical Error: The AI failed to output valid JSON routing.", ui_debug_log

    return "Agentic Loop Halted: AI exceeded maximum allowed debugging turns.", ui_debug_log

# ==========================================
# SELF-TESTING THE AGENT (Run this file directly!)
# ==========================================
if __name__ == "__main__":
    # Test 1: The Stack Overflow (Infinite Recursion)
    segfault_code = """
    #include <iostream>

    int main() {
        int numbers[3] = {10, 20, 30};
        
        // BUG: Looping too far (i <= 3 means it accesses numbers[3] which doesn't exist)
        for(int i = 0; i <= 3; i++) {
            numbers[i] = numbers[i] * 2;
        }
        
        return 0;
    }
    """
    
    # Use PERMANENT file names so macOS stops asking for permission
    source_file = "debug_target.cpp"
    executable = "./debug_target.bin"
    
    print("[SYSTEM] Compiling with '-g' debug symbols...")
    with open(source_file, "w") as f:
        f.write(segfault_code)
    
    # Compile and overwrite the permanent binary
    subprocess.run(["g++", "-g", source_file, "-o", executable.strip("./")])
    
    # 2. Trigger the Agentic Loop
    final_diagnosis = agentic_debug_loop(
        cpp_code=segfault_code, 
        crash_output="Segmentation fault: 11", 
        executable_path=executable
    )
    
    print("\n==========================================")
    print("🎓 FINAL AI DIAGNOSIS SENT TO UI:")
    print("==========================================")
    print(final_diagnosis)
    
    # 🚫 WE NO LONGER DELETE THE FILES! 
    # Leaving them here stops macOS from spamming permission popups.
    
