# PROJECT_DEEP_CONTEXT

Status: IMPLEMENTED codebase audit based on actual source files, not the existing project summary.

This document is the source-of-truth analysis for the current workspace implementation. When the project documentation and the implementation differ, the implementation is treated as authoritative.

## 0. Scope and methodology

This audit inspected the actual code in the workspace, including:

- the Streamlit app in the project folder
- the compiler and execution service
- security scanning
- classifier and prompt generation
- AI fix validation
- LLDB agent loop
- logging and metrics files
- sample C++ test inputs
- dependency and file-system artifacts

Important rule applied throughout:

- If a behavior is implemented in code, it is treated as IMPLEMENTED.
- If only partial implementation exists, it is marked PARTIAL.
- If no code path exists, it is marked MISSING.
- If the repository does not contain enough evidence, it is marked UNKNOWN.
- If a conclusion is a reasonable interpretation from code but not directly proven, it is marked INFERRED.

No secrets were found in the inspected code. Any future secret-like values should be treated as a red flag and stored in environment variables instead of source files.

---

## 1. CURRENT CODEBASE INVENTORY

### 1.1 Workspace inventory

| File | Responsibility | Important Functions | Dependencies | Used By | Side Effects | Status |
|---|---|---|---|---|---|---|
| Chatbot-Driven-Compiler-Debugger/app.py | Streamlit app / orchestrator | get_ai_explanation(), stop_and_save_metrics(), reset_execution_state(), main UI flow | streamlit, requests, codecarbon, fix_engine, secure_scan, error_classifier, compiler_service, audit_logger, error_logger, lldb_engine | User interaction | Writes session state, logs interactions, writes runtime files, may rerun UI, tracks emissions | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/compiler_service.py | Compile and run C++ source | compile_and_run() | subprocess, os | app.py | Writes temp_source.cpp, launches g++, executes temp_program | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/secure_scan.py | Regex denylist security scan | run_security_guardrail() | re | app.py, fix_engine.validate_ai_fix() | None beyond returning a boolean and message | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/error_classifier.py | Rule-based compiler error classification and prompt creation | classify_error(), get_diagnostic_prompt() | none | app.py | Produces AI diagnostic prompt | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/fix_engine.py | Extract AI-generated code, validate it using security scan | validate_ai_fix() | re, secure_scan | app.py | None beyond scanning extracted code | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/lldb_engine.py | LLDB debugging loop using Ollama as orchestrator | execute_lldb_command(), agentic_debug_loop() | subprocess, requests, json, os | app.py | Executes LLDB commands on executable_path and returns log text | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/audit_logger.py | Append JSON interaction log history | log_interaction() | json, os, datetime | app.py | Appends to system_interactions.json | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/error_logger.py | Parse compiler errors and append CSV log | initialize_log_file(), parse_gcc_error(), log_error() | csv, re, os, datetime | app.py | Appends to compiler_errors.csv | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/requirements.txt | Dependency declaration | none | none | environment setup | None | PARTIAL |
| Chatbot-Driven-Compiler-Debugger/README.md | Human docs | none | none | project readers | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/PROJECT_ANALYSIS.md | Earlier project analysis / narrative | none | none | human review | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/final_tests.cpp | Demo scenarios for security, syntax, runtime, logic review | none | none | human/verification | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/test1.cpp | Example compile failure due to sort comparator | none | none | human/verification | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/verify_cpp.py | Helper script to exercise compile_and_run() | none | compiler_service | direct execution | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/compile_check.py | Same helper script pattern | none | compiler_service | direct execution | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/check.cpp | Temporary/auxiliary compile test | none | none | manual use | writes compiled check.exe in parent root | PARTIAL |
| Chatbot-Driven-Compiler-Debugger/.gitignore | Ignore patterns for generated files | none | none | git | None | PARTIAL |
| Chatbot-Driven-Compiler-Debugger/system_interactions.json | Runtime chat log file | none | none | audit_logger, app.py | JSON log accumulation | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/compiler_errors.csv | Historical compiler errors log | none | none | error_logger | CSV append | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/emissions.csv | CodeCarbon measurements | none | none | CodeCarbon | CSV append | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/github_link.txt | Repository URL | none | none | human reference | None | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/github_risk_data.csv | Security-event data | none | none | human reference | none | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/temp_source.cpp | Runtime temporary source file | none | none | compiler_service | created by compile_and_run() | IMPLEMENTED |
| Chatbot-Driven-Compiler-Debugger/temp_program | Runtime executable | none | none | compiler_service and lldb_engine | created by compile_and_run() and used by LLDB | IMPLEMENTED |
| c:/TOACD_PROJECT/check.exe | Root-level compiled artifact | none | system compiler | manual compile testing | created outside app folder | PARTIAL |

### 1.2 What is actually used vs orphaned

Observed actual runtime usage from code:

- app.py imports and calls all major modules.
- compiler_service.py is used directly by app.py.
- secure_scan.py is used by app.py and by fix_engine.validate_ai_fix().
- error_classifier.py is used by app.py.
- audit_logger.py is used by app.py.
- error_logger.py is used by app.py.
- lldb_engine.py is used by app.py.
- fix_engine.py is used by app.py.

The root-level helper scripts appear to be ad hoc checks and are not integrated into the main flow.

---

## 2. ACTUAL EXECUTION GRAPH

### 2.1 A. Successful compilation

User code in Streamlit editor
↓
app.py
↓
run_security_guardrail() in secure_scan.py
↓
If safe: compile_and_run() in compiler_service.py
↓
Write temp_source.cpp
↓
Run g++ -g temp_source.cpp -o temp_program
↓
Execute temp_program
↓
If return code == 0: success path
↓
Display stdout in UI
↓
Call get_ai_explanation() with logical review prompt
↓
Store assistant response in st.session_state.messages
↓
log_interaction(..., error_category="Logic Check")
↓
stop_and_save_metrics() saves CodeCarbon records
↓
st.rerun()

### 2.2 B. Compilation failure

User code in editor
↓
app.py
↓
run_security_guardrail()
↓
If safe: compile_and_run()
↓
Compilation returns non-zero status; app.py enters compile-error branch
↓
log_error(stderr, user_code)
↓
classify_error(stderr)
↓
get_diagnostic_prompt() builds strict prompt
↓
get_ai_explanation(prompt)
↓
validate_ai_fix(explanation)
↓
Extract ```cpp code block using regex
↓
Run security scan on extracted code
↓
Display explanation and optionally corrected code in UI
↓
log_interaction(..., fixed_code=suggested_code)

### 2.3 C. Runtime failure

User code in editor
↓
app.py
↓
compile_and_run()
↓
Executable exits with return code < 0 or is otherwise classified as runtime crash by app.py when status == 2
↓
app.py calls agentic_debug_loop(cpp_code, crash_output, executable_path="./temp_program")
↓
LLM must return JSON with thought/action/command/explanation
↓
If action == "lldb_command": execute_lldb_command() runs lldb -b -o run -o <command> <executable>
↓
Return LLDB output to current_context
↓
Next loop turn continues
↓
If action == "final_fix": return final diagnosis and UI log
↓
Display diagnosis and log in Streamlit UI

### 2.4 D. AI-generated fix

Compiler error diagnosis from app.py
↓
Model returns explanation and code block
↓
validate_ai_fix() finds code block with regex r'```cpp\n(.*?)\n```'
↓
Security scan runs on extracted code only
↓
No recompilation of generated code occurs
↓
No execution of fixed code occurs
↓
No semantic validation occurs
↓
Only a regex-based security check is performed

### 2.5 E. Follow-up chatbot

User types question in chat
↓
app.py appends user message to st.session_state.messages
↓
Builds conversation_history from user code + last 4 messages
↓
Calls get_ai_explanation(conversation_history)
↓
AI returns natural-language answer
↓
Response appended as assistant follow-up message
↓
log_interaction(..., error_category="Follow-up Chat")

### 2.6 F. LLDB debugging

The app does not run a full debugger session with interactive breakpoints. Instead it uses a single-shot Ollama-driven loop:

- lldb_engine.execute_lldb_command() runs #[lldb -b -o run -o command executable]
- The command is selected by the LLM
- The command is executed as a subprocess argument list
- Output is returned as stdout only
- The model has up to 2 turns before stopping

### 2.7 G. Logging

- app.py triggers log_interaction() for user chat and compile results
- app.py triggers log_error() for compile failures
- CodeCarbon tracker is started and stopped in app.py, writing emissions.csv

### 2.8 H. Energy measurement

app.py starts:

- tracker = EmissionsTracker(project_name="llama3_debugger", log_level="error")
- tracker.start()
- stop_and_save_metrics() obtains tracker.stop() and writes green_metrics into session state
- emissions.csv stores CodeCarbon output

---

## 3. FUNCTION-LEVEL ANALYSIS

### 3.1 app.py

Function: get_ai_explanation(prompt)
File: Chatbot-Driven-Compiler-Debugger/app.py
Purpose: Send a prompt to the local Ollama API and return text.
Inputs: prompt string
Input types: str
Output: model response string or connection error text
Output type: str
Exceptions: requests exceptions are caught
External dependencies: requests, Ollama at localhost:11434
Side effects: HTTP POST to local model
Called by: success-path logic review, compile-error analysis, follow-up chat
Calls: none in this function beyond requests.post
Potential problems: no timeout, no retry, no explicit JSON validation, no error handling for malformed Ollama responses

Function: stop_and_save_metrics(tracker, start_t)
File: Chatbot-Driven-Compiler-Debugger/app.py
Purpose: Stop CodeCarbon tracker and store metrics in session state.
Inputs: tracker object, start timestamp
Input types: object, float
Output: st.session_state.green_metrics
Output type: dict
Exceptions: catches broad Exception
External dependencies: codecarbon
Side effects: modifies Streamlit session state
Called by: all app flows after compiler execution
Calls: tracker.stop()
Potential problems: fallback values hardcoded, broad exception swallow, metrics may be stale or misleading

Function: reset_execution_state()
File: Chatbot-Driven-Compiler-Debugger/app.py
Purpose: Clear runtime status variables
Inputs: none
Input types: none
Output: None
Output type: None
Exceptions: none
External dependencies: st.session_state only
Side effects: modifies session state
Called by: initial setup and when the editor code changes
Calls: none
Potential problems: only resets a subset of runtime state; may leave stale values behind

Function: main Streamlit app flow (not a named function)
File: Chatbot-Driven-Compiler-Debugger/app.py
Purpose: orchestrate security scan, compile/run, classification, AI feedback, LLDB, logging, UI
Inputs: user code from st.text_area and chat messages
Input types: str, session state dict
Output: UI updates, logs, messages
Output type: UI / state mutation
Exceptions: handled partially; broad st.stop() on unsafe code
External dependencies: all project modules plus external compiler and model
Side effects: writes temp files, logs, reruns, modifies chat session and metrics
Called by: Streamlit runtime
Calls: run_security_guardrail, compile_and_run, classify_error, get_diagnostic_prompt, get_ai_explanation, validate_ai_fix, agentic_debug_loop, log_interaction, log_error
Potential problems: repeated initialization, duplicated UI blocks, stale state, no actual validation of generated fix code, no sandboxing

### 3.2 compiler_service.py

Function: compile_and_run(cpp_code)
File: Chatbot-Driven-Compiler-Debugger/compiler_service.py
Purpose: Compile source code and execute the binary.
Inputs: cpp_code string
Input types: str
Output: tuple(status_code, stdout, stderr)
Output type: tuple[int, str, str]
Exceptions: subprocess.TimeoutExpired and generic Exception
External dependencies: g++, OS execution
Side effects: writes temp_source.cpp and temp_program, executes binary
Called by: app.py
Calls: subprocess.run
Potential problems: treats any non-negative exit code as success; no distinction between normal nonzero exit and runtime crash; no cleanup; no resource limits; no path isolation; no check for malicious shell execution; no timeout on the compiled program beyond 5 seconds only by subprocess.run

### 3.3 secure_scan.py

Function: run_security_guardrail(code_snippet)
File: Chatbot-Driven-Compiler-Debugger/secure_scan.py
Purpose: Reject obviously unsafe C++ patterns.
Inputs: C++ source string
Input types: str
Output: (is_safe, warning_message)
Output type: tuple[bool, str]
Exceptions: none
External dependencies: re
Side effects: none besides returning warning
Called by: app.py, fix_engine.validate_ai_fix()
Calls: re.search
Potential problems: regex-based denylist, not a real sandbox, can false-positive in comments/strings, can miss macros, can be bypassed by obfuscation or alternate APIs

### 3.4 error_classifier.py

Function: classify_error(error_message)
File: Chatbot-Driven-Compiler-Debugger/error_classifier.py
Purpose: Map GCC error text to heuristic categories.
Inputs: compiler stderr text
Input types: str
Output: (category, strategy)
Output type: tuple[str, str]
Exceptions: none
External dependencies: none
Side effects: none
Called by: app.py
Calls: none
Potential problems: simplistic keyword matching; may misclassify unrelated messages; does not parse structured GCC diagnostics

Function: get_diagnostic_prompt(category, strategy, code, error)
File: Chatbot-Driven-Compiler-Debugger/error_classifier.py
Purpose: Build a constrained prompt for the model.
Inputs: category, strategy, code, compiler error
Input types: str, str, str, str
Output: a prompt string
Output type: str
Exceptions: none
External dependencies: none
Side effects: none
Called by: app.py
Calls: none
Potential problems: prompt is long but still only textual; no strict output validation beyond formatting instructions; AI can still ignore instructions

### 3.5 fix_engine.py

Function: validate_ai_fix(llm_response)
File: Chatbot-Driven-Compiler-Debugger/fix_engine.py
Purpose: Extract code block from LLM output and run a security regex scan.
Inputs: LLM response text
Input types: str
Output: (is_safe, extracted_code, security_warning)
Output type: tuple[bool, str | None, str]
Exceptions: none besides regex not matching
External dependencies: re, run_security_guardrail
Side effects: none
Called by: app.py
Calls: run_security_guardrail
Potential problems: no code compilation, no execution, no semantic test, no validation of multiple blocks or format compliance beyond one regex

### 3.6 lldb_engine.py

Function: execute_lldb_command(executable_path, command)
File: Chatbot-Driven-Compiler-Debugger/lldb_engine.py
Purpose: Run one LLDB command in batch mode against an executable.
Inputs: executable_path string, command string
Input types: str, str
Output: stdout text from LLDB
Output type: str
Exceptions: subprocess.TimeoutExpired and broader Exception
External dependencies: lldb executable, OS process
Side effects: executes system-level debugger command
Called by: agentic_debug_loop
Calls: subprocess.run
Potential problems: command string is not sanitized, command injection risk if LLM is malicious or model returns bad command; no validation of allowed commands; no assurance target file is safe to inspect

Function: agentic_debug_loop(cpp_code, crash_output, executable_path)
File: Chatbot-Driven-Compiler-Debugger/lldb_engine.py
Purpose: ReAct loop where model chooses LLDB command or final fix.
Inputs: source code, runtime crash output, executable path
Input types: str, str, str
Output: (final_diagnosis, ui_debug_log)
Output type: tuple[str, str]
Exceptions: json.JSONDecodeError caught
External dependencies: requests, json, Ollama, lldb
Side effects: calls external model and executes command subprocesses
Called by: app.py
Calls: execute_lldb_command(), requests.post()
Potential problems: no timeouts on HTTP requests; assumes JSON response; allows up to 2 turns only; not very robust against malformed or malicious model output

### 3.7 audit_logger.py

Function: log_interaction(user_prompt, llm_response, code_snippet, error_category, fixed_code)
File: Chatbot-Driven-Compiler-Debugger/audit_logger.py
Purpose: Append a conversational turn to a JSON log.
Inputs: user prompt, model response, code, error category, fixed code
Input types: str, str, str, str, str
Output: none
Output type: None
Exceptions: catches JSONDecodeError on load, then resets to []
External dependencies: json, os, datetime
Side effects: reads and writes system_interactions.json
Called by: app.py
Calls: json.load, json.dump
Potential problems: file is read/written every turn; concurrency issues if multiple users or multiple processes access it simultaneously

### 3.8 error_logger.py

Function: parse_gcc_error(raw_error_text)
File: Chatbot-Driven-Compiler-Debugger/error_logger.py
Purpose: parse GCC error string into line, severity, message.
Inputs: raw compiler error text
Input types: str
Output: (line_num, severity, clean_message)
Output type: tuple[str, str, str]
Exceptions: none
External dependencies: re
Side effects: none
Called by: log_error
Calls: re.search
Potential problems: regex only matches temp_code.cpp and not temp_source.cpp; inconsistent with actual compiler output; line numbers may be Unknown

Function: log_error(raw_error_text, user_code)
File: Chatbot-Driven-Compiler-Debugger/error_logger.py
Purpose: Save compiler failure metadata to CSV.
Inputs: raw GCC error and user code
Input types: str, str
Output: clean_message string
Output type: str
Exceptions: broad except around snippet extraction
External dependencies: csv, datetime, os
Side effects: writes compiler_errors.csv
Called by: app.py
Calls: initialize_log_file(), parse_gcc_error()
Potential problems: may log partial data with wrong line numbers; writes only raw text and snippet, not full execution context

---

## 4. COMPLETE DATA FLOW

The real data flow in this repo is:

User C++ code
→ secure_scan.py run_security_guardrail()
→ compiler_service.compile_and_run()
→ writes temp_source.cpp
→ invokes g++
→ captures stdout/stderr
→ app.py branches by status code
→ if compile error: classifier + prompt + Ollama + fix validation
→ API response text
→ regex extraction of ```cpp blocks
→ security scan of extracted patch
→ UI display
→ log_interaction() persistence

Additional runtime path:

User C++ code
→ compiler_service.compile_and_run()
→ executes temp_program
→ stdout/stderr from process
→ app.py decides runtime crash path
→ lldb_engine.agentic_debug_loop()
→ requests.post to local Ollama
→ LLM returns JSON decision
→ execute_lldb_command() invokes lldb
→ lldb output is fed back into the model
→ final explanation is displayed and logged

Data format and transformation summary:

- Input C++ source: raw string from Streamlit text_area.
- Security scan: regex denylist over source text.
- Compilation: writes source to file and invokes compiler subprocess.
- Compiler output: text captured from subprocess stdout/stderr.
- Error classifier: string matching against compiler stderr.
- LLM prompt: plain text prompt built by f-strings.
- LLM response: plain text from API; sometimes required to be JSON, but not enforced at runtime.
- Fix validation: regex extraction of markdown code block.
- Storage: JSON array in system_interactions.json, CSV in compiler_errors.csv, CodeCarbon CSV in emissions.csv.

Validation and failure points:

- Source validation is regex-based, not semantic validation.
- AI fix validation stops after security regex; it does not compile or test code.
- LLM responses are not JSON-validated except in LLDB agent loop, and there is no robust error recovery for invalid LLM responses.
- Compiler and runtime errors are stored but not always parsed reliably.

---

## 5. OLLAMA / LLM ANALYSIS

### 5.1 In-scope LLM use

The codebase uses Ollama in the following places:

- app.py: get_ai_explanation()
- lldb_engine.py: agentic_debug_loop()

### 5.2 Actual API contract

| Service | Endpoint | Method | Request | Response | Timeout | Retry | Error Handling |
|---|---|---|---|---|---|---|---|
| Ollama | http://localhost:11434/api/generate | POST | {"model": MODEL_NAME, "prompt": prompt, "stream": false} | JSON object with response field | None explicitly set | None implemented | Checks status_code == 200; otherwise returns connection error text |
| Ollama LLDB loop | http://localhost:11434/api/generate | POST | {"model": MODEL_NAME, "prompt": current_context, "stream": false, "format": "json"} | JSON response text expected to parse as object | None explicitly set | None implemented | JSONDecodeError caught and returns diagnostic failure |

### 5.3 Model and prompt construction

Model used:

- MODEL_NAME = "llama3"
- set in both app.py and lldb_engine.py

Prompt construction examples:

- Logic review: "Review this C++ code for logical flaws. If it's perfect, say so. CODE: ..."
- Compile-diagnostic prompt: generated by get_diagnostic_prompt() in error_classifier.py
- LLDB agent prompt: explicitly says the model is an "Autonomous C++ Debugger running on Apple Silicon"

System prompt and user prompt behavior:

- The code does not use a separate system field; it sends the prompt as a single plain-text string under text generation API.
- The app uses direct user prompt injection patterns and context strings assembled in code.

Context included:

- In follow-up chat: user code + last four messages + latest question.
- In runtime LLDB loop: source code + crash output + LLDB result history + previous reasoning.
- In compile-fix prompt: code + compiler error + category + strategy.

Response parsing:

- app.py treats the model output as plain text.
- fix_engine.py extracts code blocks using regex.
- lldb_engine.py parses JSON via json.loads(response).

Error handling:

- HTTP errors are returned as strings: "Error connecting to Ollama: ..." or "Connection Failed. Is Ollama running? Error: ..."
- Malformed JSON in the LLDB flow triggers "Critical Error: The AI failed to output valid JSON routing."

Timeout/retry risks:

- No explicit requests timeout parameter.
- No retry/backoff.
- No circuit breaker.
- No handling for slow or unresponsive local server.

Context size and model limitations:

- No explicit max context configuration.
- No tokenizer protection.
- No truncation strategy.

### 5.4 Risk assessment

- Prompt injection risk: HIGH, because user code and compiler output are fed directly to the model with little sanitization.
- Untrusted compiler output in prompts: YES, compiler stderr is inserted verbatim into prompts.
- Untrusted user code in prompts: YES, code is inserted directly into prompts.
- Malformed model responses: present in LLDB JSON path and possible in fix extraction path.
- JSON parsing failures: present in lldb_engine.py.
- Missing timeouts: yes.
- Missing retries: yes.
- Model state retention: none; each request is stateless except conversation history built in the chat UI.

---

## 6. PROMPT ENGINEERING AUDIT

### 6.1 Prompt inventory

| Prompt | Where | Purpose | Inputs | Output format requested | Parsed? | Validated? | Status |
|---|---|---|---|---|---|---|---|
| Logic review prompt | app.py get_ai_explanation() | Review C++ code for flaws | user_code | plain text response | no | no | IMPLEMENTED |
| Compile diagnostic prompt | error_classifier.get_diagnostic_prompt() | Explain compile errors and produce secure fix | category, strategy, code, compiler error | markdown with EXPLANATION and SECURE FIXED CODE | yes, regex in fix_engine | partial (security scan only) | IMPLEMENTED |
| LLDB agent prompt | lldb_engine.agentic_debug_loop() | choose next debugger action or final fix | cpp_code, crash_output, previous LLDB output | strict JSON with thought/action/command/explanation | yes, json.loads() | partial; no command allowlist | IMPLEMENTED |
| Follow-up chat prompt | app.py follow-up conversation | answer user question in context | user code + last messages + latest question | plain text | no | no | IMPLEMENTED |

### 6.2 Prompt weaknesses

Observed weaknesses in the code:

- Ambiguous instructions: the prompt tells the model to be strict and also tells it to fix code while preserving original logic; not all constraints are enforced.
- Conflicting instructions: some prompts tell the model to output only a certain format, but the app still allows plain text responses in several places.
- Excessive context: follow-up prompts include code + history; size can grow with repeated messages.
- Missing constraints: no length limit or output schema enforcement except LLDB loop.
- Lack of structured output: general logic and fix prompts are plain text.
- Prompt injection: user code and compiler output are inserted with minimal escaping.
- Missing validation: model output is accepted without robust schema or semantic verification.
- Hallucination risk: the app accepts model-generated fixes without compilation or execution verification.

---

## 7. COMPILER PIPELINE AUDIT

### 7.1 Actual compiler command

From compiler_service.py:

`g++ -g temp_source.cpp -o temp_program`

This is executed via subprocess.run with capture_output=True, text=True, timeout=10.

### 7.2 Compiler behavior and handling

What the current code handles:

- compile failure via returncode != 0
- runtime crash detection by returncode < 0
- writing temp source file
- running the compiled executable
- capturing stdout and stderr
- timeouts via subprocess.TimeoutExpired with return -1

What it does not handle well:

- normal nonzero exits are treated as success because only negative exit code triggers runtime crash classification
- no different handling for signal termination, abort, floating-point exceptions, or exit status 134/139 etc.
- no distinction between segmentation fault, divide by zero, infinite loop, memory violation, or nonzero exit due to user logic
- no file cleanup after compile/run
- no resource quotas
- no process isolation
- no `timeout` on external process except a 5-second limit, but no proper output truncation control
- no containerized limits or sandbox

### 7.3 Edge-case analysis

| Edge case | Current handling | Status |
|---|---|---|
| Compile timeout | subprocess.run(..., timeout=10) then return -1 | IMPLEMENTED |
| Runtime timeout | subprocess.run(..., timeout=5) then return -1 | IMPLEMENTED |
| Segmentation fault | returncode < 0 indicates crash; may vary by platform | PARTIAL |
| Abort | not explicitly identified | MISSING |
| Signal termination | captured in returncode < 0 only | PARTIAL |
| Normal nonzero exit | treated as success | IMPLEMENTED but incorrect for many real cases |
| Infinite loop | timeout return -1; no evidence of detection beyond timeout | PARTIAL |
| Huge output | no truncation | MISSING |
| Huge memory allocation | no limits | MISSING |
| Fork bomb | no limits | MISSING |
| File creation | allowed by default | MISSING |
| Network access | allowed by default | MISSING |
| Malicious shell commands | not prevented by code execution; source scan is regex only | MISSING |

---

## 8. SECURITY ARCHITECTURE AUDIT

### 8.1 Real attack surface

The project intentionally compiles and runs user-provided C++ code with the OS permissions of the Python process. This is the single biggest security risk.

The actual code does not provide:

- subprocess isolation
- filesystem chroot or sandbox
- network blocking
- environment sandboxing
- permission reduction
- CPU quota enforcement
- memory quotas
- process or file descriptor limits
- syscall filtering
- containerization
- user account drop

The security scanner is only a regex denylist. It catches known dangerous expressions in source text, but it does not block compiled arbitrary behavior.

### 8.2 Attack surface details

- `compile_and_run()` launches the compiler directly using `subprocess.run` with no sandbox or `setuid` restrictions.
- The compiled executable is run with default OS permissions.
- The code can create or overwrite files in the working directory.
- It can read files accessible to the Python user.
- It can spawn child processes through the OS if `system()` or `popen()` are not filtered.
- It can make network calls if the system allows them.
- It can consume CPU and memory without quota caps.
- It can block process execution under timeout, but that is not security isolation.

The project is best described as a constrained educational prototype, not a secure execution sandbox.

### 8.3 Defensive recommendations

- Run untrusted code in a dedicated sandboxed container or restricted VM.
- Reduce permissions for the worker user.
- Mount a read-only workspace and a disposable temp directory.
- Block network access by default.
- Use cgroups / resource limits / seccomp / AppArmor / SELinux / sandbox tools.
- Add runtime logging and kill switches for runaway processes.
- Move generated artifacts to a per-session directory to avoid collisions.
- Treat AI-generated code as untrusted and compile it in the same restricted environment.

---

## 9. CONCURRENCY / MULTI-USER ANALYSIS

The app uses fixed file names in the project directory:

- temp_source.cpp
- temp_program
- debug_target.cpp / debug_target.bin in self-test code
- system_interactions.json
- compiler_errors.csv
- emissions.csv

This creates actual collision risk:

- If two users or two runs happen at the same time, they share the same temporary source and binary path.
- `audit_logger.py` reads the whole JSON log file, appends, and writes it back. This is not atomic and can lose updates under concurrent writes.
- `error_logger.py` appends to a single CSV file without synchronization.
- The app stores session state in Streamlit memory, but process-level concurrency in multi-user deployments would create separate server sessions; the fixed files remain shared.

Possible shared-state problems:

- race conditions on temp_source.cpp and temp_program
- cross-user state leakage if file names overlap
- logs and metrics interleave across runs
- LLDB process uses same executable path, causing collisions in multi-user execution

The current design is not safe for concurrent multi-user use without per-run directories and per-run task IDs.

---

## 10. STREAMLIT STATE ANALYSIS

Key session state values:

- st.session_state.messages
- st.session_state.last_status
- st.session_state.last_stdout
- st.session_state.last_stderr
- st.session_state.last_agent_log
- st.session_state.green_metrics
- st.session_state.last_compiled_code
- st.session_state.editor_code

Observed patterns:

- messages are initialized twice in app.py:
  - first: if "messages" not in st.session_state
  - then again: same check duplicated
- reset_execution_state() is called when code changes, but it only resets selected state variables.
- The app calls st.rerun() after compile actions to refresh the UI.
- `last_agent_log` is shown in a duplicated block in the UI.
- `messages` are appended for compile success, runtime crash, compile failure, and follow-up chat.
- The app uses a chat-like history but does not enforce a strict schema for message objects.

Potential issues:

- duplicated initialization
- stale runtime results after user edits
- repeated rerun behavior
- possible race between UI re-render and appended message mutations

---

## 11. API / SERVICE CONTRACTS

### 11.1 Service inventory

| Service | Endpoint | Method | Request | Response | Timeout | Retry | Error Handling |
|---|---|---|---|---|---|---|---|
| Ollama generate | http://localhost:11434/api/generate | POST | model, prompt, stream, format (LLDB-only) | JSON with response text | none | none | status code check and exception catch |
| Local compiler | g++ executable | subprocess invocation | source path and output binary path | stdout/stderr, return code | 10s compile, 5s run | none | subprocess error catch and return -1 |
| LLDB debugger | lldb executable | subprocess invocation | -b -o run -o <command> <executable> | stdout text | 10s | none | TimeoutExpired catch |
| CodeCarbon | Python library | library call | tracker.start()/stop() | emissions metrics | none | none | fallback fallback values |

---

## 12. FILE SYSTEM ANALYSIS

| File | Created By | Read By | Modified By | Deleted? | Purpose | Risk |
|---|---|---|---|---|---|---|
| temp_source.cpp | compiler_service.compile_and_run() | compiler_service itself, user/compiler logs | overwritten on every compile | no | transient source for g++ | collisions between runs |
| temp_program | compiler_service.compile_and_run() | lldb_engine.execute_lldb_command, app.py runtime path | overwritten on every compile | no | compiled executable | collisions and stale artifact risk |
| system_interactions.json | audit_logger.log_interaction() | app.py load existing log, user review | appended every interaction | no | conversation history | unsynchronized writes, JSON corruption risk |
| compiler_errors.csv | error_logger.log_error() | manual review | appended on each compile error | no | error dataset | wrong regex for temp_code.cpp |
| emissions.csv | CodeCarbon tracker | user review | appended by CodeCarbon | no | energy metrics | log file growth |
| github_risk_data.csv | not clearly used in runtime | unknown | unknown | unknown | historical security analysis | not connected to runtime |
| debug_target.cpp | self-test block in lldb_engine.py | manual debug test | created by self-test | no | LLDB demonstration | stale artifact |
| debug_target.bin | self-test block | LLDB self-test | created by self-test | no | LLDB demonstration | stale artifact |
| .DS_Store | OS metadata | none ordinarily | OS | no | macOS metadata | benign but noisy |
| __pycache__ | Python runtime | imported modules | generated automatically | usually no | compiled Python cache | not security-sensitive |

### 12.1 Generated files not consistently cleaned up

The code writes files into the project directory and does not remove them after use. This is a practical issue for local development and a race hazard for multiple users.

---

## 13. DEPENDENCY AUDIT

### 13.1 requirements.txt vs actual imports

The actual code imports:

- streamlit
- requests
- codecarbon
- subprocess (stdlib)
- os (stdlib)
- json (stdlib)
- re (stdlib)
- csv (stdlib)
- datetime (stdlib)

But requirements.txt contains only:

- streamlit

This is a direct mismatch. The repo requires at least:

- streamlit
- requests
- codecarbon

### 13.2 External executables needed

| Tool | Required | Purpose | How detected |
|---|---|---|---|
| Python | runtime environment | execute app | imported interpreter |
| g++ | compile C++ code | compiler service | subprocess call in compiler_service.py |
| lldb | runtime debugging | LLDB agent | subprocess call in lldb_engine.py |
| Ollama | local model server | LLM generation | requests to localhost:11434 |
| model llama3 | LLM model | actual response generation | MODEL_NAME = "llama3" |

### 13.3 Dependency risk

- Missing packages are not declared in requirements.txt.
- Python packages are not version-pinned.
- System tool availability is assumed but not checked at startup.
- If Ollama is absent, app.py still renders UI but API requests fail with a user-facing connection error.

---

## 14. CROSS-PLATFORM AUDIT

| Component | macOS | Linux | Windows | Problem |
|---|---|---|---|---|
| g++ | usually present via Xcode/CLT | common via build-essential | MinGW/LLVM required | not guaranteed on Windows |
| LLDB | available via Xcode | common in clang toolchain | separate installation required | app assumes macOS toolchain |
| temp_program executable name | ./temp_program works | ./temp_program works | ./temp_program may not execute directly on Windows | cross-platform mismatch |
| shell behavior | POSIX shell-like conventions | POSIX-like | CMD/PowerShell differences | not all subprocess expectations are platform-neutral |
| crash detection | negative exit codes may map to signals | system-specific | different signal mapping | compile_and_run() is not robust |
| permissions | standard user env | standard user env | Windows ACLs differ | no normalization |
| default path style | /tmp-like or project dir paths | /tmp-ish or project dir | drive letters and backslashes | file path assumptions differ |
| AI prompt wording | references Apple Silicon and macOS | less explicit | not adapted | some prompts are explicitly macOS-oriented |

Overall status: PARTIAL portability. The repo appears to be built and validated around a macOS-like local environment, not a general cross-platform workflow.

---

## 15. TESTING GAP ANALYSIS

### 15.1 Existing tests

The workspace contains example C++ inputs but not a formal test suite:

- final_tests.cpp: four scenarios
- test1.cpp: one compiler-failure example
- verify_cpp.py and compile_check.py: direct compile checks

### 15.2 Missing tests

| Functionality | Existing Test | Missing Test | Risk |
|---|---|---|---|
| security bypass detection | sample malicious code in final_tests.cpp | real fuzzing and bypass tests | regex logic can be bypassed |
| compile failure handling | some syntax examples | multi-error GCC parsing | misclassification likely |
| runtime handling | runtime error example exists | signals, nonzero returns, divide-by-zero, aborted processes | return-code semantics are brittle |
| timeout handling | none | compile timeout, run timeout, hanging program | timeouts are not validated |
| AI response parsing | partial | malformed JSON, multiple code blocks, no code block | parser is fragile |
| LLDB | no end-to-end tests | command validation, loop exit, malformed outputs | LLM can execute arbitrary commands |
| logging | some manual logs exist | file corruption and concurrent-write tests | data loss under load |
| concurrency | none | two-user simultaneous compile/run | race conditions likely |
| malformed input | none | huge source, weird encoding, empty code | app may behave unpredictably |
| AI-generated code validation | none | compile generated code, run it, compare results | unchecked generated code |

There is no automated pytest or other real regression suite in the repo.

---

## 16. BUG DISCOVERY

### 16.1 Suspected bugs and evidence

| File | Function | Evidence | Why it is a problem | Impact | Confidence | Suggested direction |
|---|---|---|---|---|---|---|
| compiler_service.py | compile_and_run() | if run_process.returncode < 0: crash else success | Any nonzero exit status (e.g., 1) is treated as success | misclassifies user-defined nonzero exits | HIGH | differentiate by signal or explicit runtime crash semantics |
| error_logger.py | parse_gcc_error() | regex looks for temp_code.cpp | actual code writes temp_source.cpp | line numbers often logged as Unknown | HIGH | match actual file name or parse generic GCC output |
| app.py | compile path and UI | st.session_state.messages initialized twice | repeated initialization is redundant | stale drift / confusing state | HIGH | keep a single init block |
| lldb_engine.py | execute_lldb_command() | command passed into subprocess without validation | LLM-chosen command executes directly | arbitrary debugger command execution | HIGH | allowlist commands and sanitize input |
| app.py | get_ai_explanation() | no requests timeout or retry | local Ollama may hang or fail | hangs and poor UX | HIGH | add timeout and retry/backoff |
| secure_scan.py | run_security_guardrail() | regex denylist uses source text checks | comments and strings may trigger false positives; code can bypass regex | bad blocking and false trust | HIGH | use AST or sandbox isolation |
| audit_logger.py | log_interaction() | read-modify-write entire JSON file | concurrent writes can clobber JSON state | data loss under load | MEDIUM | use per-request append with locking or DB |
| app.py | streamlit flow | st.rerun() after every action | forced rerun patterns can duplicate events or rerender unstable state | UI instability | MEDIUM | reduce rerun calls and use proper state updates |

### 16.2 Additional implementation bug patterns

- `error_logger.log_error` writes `clean_message` into the CSV as Error_Type, even though the log header expects Error_Type. This is okay syntactically, but semantically the value is the message instead of the category.
- `app.py` defines `st.session_state.last_agent_log` twice and duplicates the LLDB section block.
- The `.gitignore` contains `system_interactions.jsongithub_scraper.py` as one concatenated token, likely a malformed ignore entry.
- There is no actual path safety or session isolation for temp files; they are created in the project folder and read by the OS process directly.

---

## 17. ARCHITECTURAL WEAKNESSES

### 17.1 Current architectural constraints

The current architecture is a single-process Streamlit app that directly orchestrates:

- UI
- file writing
- compiler invocation
- executable invocation
- runtime debugging
- remote/local LLM calls
- logging and metrics

This gives a clear educational prototype but creates tight coupling between user input, untrusted execution, and AI processing.

### 17.2 Scaling and maintainability constraints

- separation of concerns: PARTIAL; the UI and orchestration are combined in app.py
- coupling: HIGH; the UI depends directly on file names, session state, compiler behavior, LLM behavior, and debugger behavior
- testability: LOW; no automated tests for the orchestration path
- security: LOW; untrusted code runs as the same user
- observability: PARTIAL; logs exist but are not structured or strongly typed
- deployment: POOR for multi-user or production-like environments
- multi-user support: not safe due to shared file names and app-level state assumptions

### 17.3 Possible directions

- split the app into services: compile-service, llm-service, debugging-service, logging-service
- move temporary files to per-session workdirs
- add an execution broker with worker isolation
- add queueing for compile/run jobs
- add structured API contracts and schema validation
- add a proper runtime sandbox and a health check for compiler/debugger availability

---

## 18. IMPROVEMENT OPPORTUNITIES

### 18.1 Security

Current: regex denial list is not a security boundary.
Problem: actual untrusted C++ execution still runs under the app user context.
Potential improvement: isolate execution per user in a VM or container with restricted mounts and network off.
Affected files: app.py, compiler_service.py, secure_scan.py, lldb_engine.py
Complexity: High
Risk of change: High

### 18.2 Architecture

Current: monolithic Streamlit orchestrator.
Problem: business logic, UI, compiler, LLM, and debugging are tightly coupled.
Potential improvement: break into worker/service modules with job IDs and clear contracts.
Affected files: app.py, compiler_service.py, lldb_engine.py, audit_logger.py
Complexity: Medium
Risk: Medium

### 18.3 Reliability

Current: partial path handling around exit codes and invalid AI responses.
Problem: runtime classification and LLM parse failures are brittle.
Potential improvement: validate AI JSON and isolate runtime errors by signal/exit semantics.
Affected files: app.py, compiler_service.py, lldb_engine.py, fix_engine.py
Complexity: Medium
Risk: Medium

### 18.4 Performance

Current: no async or queueing, all work is synchronous in the UI thread.
Problem: compile/run calls can block the Streamlit session.
Potential improvement: move compile and debug jobs to background workers.
Affected files: app.py, compiler_service.py, lldb_engine.py
Complexity: Medium
Risk: Medium

### 18.5 UI/UX

Current: operational output is mixed into the UI and chat history.
Problem: stale state and duplicated UI sections may confuse users.
Potential improvement: separate compile output, runtime output, debug log, and chat panels with clearer states.
Affected files: app.py
Complexity: Low
Risk: Low

### 18.6 AI/LLM

Current: direct raw prompting of user code and compiler output.
Problem: poisoning, mis-formatting, and prompt injection risk.
Potential improvement: add explicit structured schemas, example outputs, and validation of code blocks.
Affected files: app.py, error_classifier.py, lldb_engine.py, fix_engine.py
Complexity: Medium
Risk: Medium

### 18.7 Compiler pipeline

Current: compile and run directly without verification of generated fixes.
Problem: generated code is not actually compiled or tested.
Potential improvement: compile the AI-generated patch in the same sandbox and validate output.
Affected files: app.py, fix_engine.py, compiler_service.py
Complexity: High
Risk: High

### 18.8 Debugging

Current: LLDB agent can execute arbitrary command strings selected by the model.
Problem: no allowlist or validation.
Potential improvement: predefine a safe command set and require structured parsing.
Affected files: lldb_engine.py
Complexity: Medium
Risk: High

### 18.9 Testing

Current: examples only, no automated tests.
Problem: regressions are easy to introduce.
Potential improvement: add unit tests for security scanner, classifier, parser, and fix validation.
Affected files: all Python modules
Complexity: Medium
Risk: Low

### 18.10 Logging

Current: CSV/JSON append-only logs without validation.
Problem: collisions, incomplete data, malformed line numbers.
Potential improvement: switch to structured logs and per-run directories.
Affected files: audit_logger.py, error_logger.py, app.py
Complexity: Medium
Risk: Low

### 18.11 Cross-platform support

Current: macOS-targeted prompts and `./temp_program` assumptions.
Problem: Windows and Linux will not behave identically.
Potential improvement: centralize executable paths and compiler detection.
Affected files: compiler_service.py, lldb_engine.py, app.py
Complexity: Medium
Risk: Medium

### 18.12 Maintainability

Current: repeated initialization and duplicated UI blocks.
Problem: code is more fragile than it should be.
Potential improvement: consolidate helpers and state resets, reduce duplication.
Affected files: app.py
Complexity: Low
Risk: Low

---

## 19. FUTURE FEATURE READINESS

| Feature | Difficulty | Architectural impact |
|---|---|---|
| multiple compiler versions | Medium | Add compiler selection abstraction in compiler_service.py and app config |
| C++ standard selection | Medium | Need flag builder and UI controls; compiler_service.py will need flags |
| stdin input | Medium | Runtime execution path must pass stdin to compiled program |
| custom compiler flags | Low to Medium | compiler_service.compile_and_run() needs a flag parameter and validation |
| test-case execution | Medium | Need harness to run multiple inputs and compare outputs |
| unit testing | Medium | Add testing framework and isolate modules from Streamlit state |
| automated fix verification | High | Must compile and run generated fixes; current design does not do this |
| multiple AI models | Medium | Add model configuration and prompt abstraction |
| cloud AI providers | Medium to High | new provider adapters and credential management |
| syntax highlighting | Low | UI change only, not core logic |
| code diff visualization | Medium | add patch rendering and compare original vs generated code |
| breakpoints | High | full debugger UX required beyond current LLDB one-off commands |
| watch variables | High | requires richer debugger state management |
| stack visualization | High | need structured LLDB output parsing |
| memory debugging | High | requires deeper debugger integration and safer execution environment |
| multi-file C++ projects | High | current model assumes single temporary source and one executable |
| persistent user accounts | High | requires auth, DB, and session persistence |
| database-backed history | Medium | needs storage layer and schema for logs and user data |

---

## 20. AI-GENERATED FIX PIPELINE

Actual pipeline:

Compiler error
→ app.py sees compile failure
→ error_classifier.classify_error(stderr)
→ get_diagnostic_prompt(category, strategy, code, stderr)
→ get_ai_explanation(prompt)
→ fix_engine.validate_ai_fix(explanation)
→ regex extracts ```cpp code block
→ security scan on extracted code
→ display in Streamlit UI

Answering the required questions:

- Is generated code actually compiled? No. validate_ai_fix() only scans regex patterns; it does not use compiler_service.compile_and_run() on the generated code.
- Is it executed? No.
- Is it tested? No. No unit tests or behavioral validation are run against the generated fix.
- Is semantic correctness verified? No.
- Is original behavior preserved? No; the code never checks whether the fix preserves semantics.
- Can the AI return invalid code? Yes, because there is no compilation gate.
- Can the AI return multiple code blocks? Yes; regex searches for the first ```cpp ... ``` block; other blocks are ignored.
- What happens if no code block exists? validate_ai_fix() returns False, None, "No valid C++ code block was found in the AI suggestion."

---

## 21. LLDB AGENT ANALYSIS

### 21.1 Exact LLDB implementation

Inputs:

- cpp_code
- crash_output
- executable_path

Prompt:

- agent_prompt string built in lldb_engine.py
- includes: buggy code, crash output, instruction to choose one action: lldb_command or final_fix
- demands strict JSON response

Allowed commands:

- The model chooses a command string from whatever text it emits.
- There is no allowlist or validation of LLDB commands.
- execute_lldb_command() sends the exact command string to `lldb -b -o run -o <command> <path>`

JSON format:

```
{
  "thought": "...",
  "action": "lldb_command" OR "final_fix",
  "command": "the exact lldb command to run (leave empty if final_fix)",
  "explanation": "... full corrected C++ code inside markdown block ..."
}
```

Command execution:

- subprocess.run(["lldb", "-b", "-o", "run", "-o", command, executable_path], capture_output=True, text=True, timeout=10)

Output handling:

- The function returns stdout from LLDB only.
- LLDB stderr is not handled separately.
- UI debug log stores output text in a markdown block.

Turn limit:

- max_turns = 2

Failure handling:

- JSONDecodeError returns a failure diagnostic
- TimeoutExpired returns a user-facing timeout message
- general Exceptions return a system error message

### 21.2 Risks and limitations

- The LLM can choose arbitrary LLDB commands because the command is not validated.
- The model can attempt to run commands that are dangerous or non-portable.
- The code treats `lldb` as trusted tool execution.
- The implementation is not a safe command sandbox.
- The app asserts a macOS-oriented agent persona and is not truly cross-platform.

---

## 22. OBSERVABILITY

Current observable data:

- Streamlit UI status and output
- chat history in st.session_state.messages
- interactive logs in system_interactions.json
- compiler error logs in compiler_errors.csv
- CodeCarbon emissions in emissions.csv
- terminal output from subprocesses when invoked directly

Missing or weak observability:

- no structured request IDs for compile/run jobs
- no correlation IDs across model requests and file writes
- no per-request timing metrics beyond carbon+latency in UI
- no clear differentiation between compile, runtime, and AI call events
- no system-level resource metrics for CPU/memory usage during execution
- no health check for Ollama service or binary availability before use
- no metrics for AI response failure or malformed JSON

---

## 23. DEVELOPMENT WORKFLOW

Based on the repo itself, the practical workflow is:

### Install

`pip install streamlit requests codecarbon`

This is supported by requirements.txt and actual imports, although the file itself is incomplete.

### Run

`streamlit run app.py`

This is the expected UI entry point from the docs and the code structure.

### Test

No formal automated test command is defined in the repository. The examples are manual C++ files and helper scripts.

### Debug

- run the Streamlit app
- inspect UI output and logs
- use lldb_engine.py self-test block or run the script directly
- inspect compiler_errors.csv and system_interactions.json

### Start Ollama

The project documents this as expected workflow, but the code does not call it automatically:

- `ollama serve`
- `ollama pull llama3`

### Load the model

The app sets:

- MODEL_NAME = "llama3"

The actual runtime expects the local Ollama server to have that model available.

### Compile C++

The compiler is launched via:

`g++ -g temp_source.cpp -o temp_program`

### Start LLDB

There is no direct project-level call to launch LLDB in app.py; instead, LLDB is launched from the Python subprocess call in lldb_engine.py.

---

## 24. EXACT ENVIRONMENT REQUIREMENTS

### 24.1 Software

| Software | Required Version | Why | How Detected |
|---|---|---|---|
| Python | not pinned in repo; runtime-dependent | app runs on Python | local environment |
| Streamlit | not pinned | UI framework | `import streamlit` |
| requests | not pinned | Ollama HTTP calls | `import requests` |
| codecarbon | not pinned | energy measurement | `from codecarbon import EmissionsTracker` |
| g++ | platform-dependent | compile C++ code | subprocess call to `g++` |
| lldb | platform-dependent | runtime debugging | subprocess call with `lldb` |
| Ollama | local service | LLM endpoint | requests to http://localhost:11434 |
| llama3 model | not pinned | model name constant | `MODEL_NAME = "llama3"` |

### 24.2 Python packages

| Package | Version | Why | Imported By |
|---|---|---|---|
| streamlit | not pinned | UI | app.py |
| requests | not pinned | Ollama HTTP requests | app.py, lldb_engine.py |
| codecarbon | not pinned | emissions tracking | app.py |

### 24.3 System tools

| Tool | Required | Purpose | How Detected |
|---|---|---|---|
| g++ | yes | compile code | subprocess call in compiler_service.py |
| lldb | yes for runtime debugger path | debug crash output | subprocess call in lldb_engine.py |
| ollama | yes for LLM | provide model service | requests to localhost:11434 |

---

## 25. CHANGE IMPACT MAP

### app.py

If modified, it can affect:

- UI flow and all compile/runtime branches
- chat logic and st.session_state behavior
- security gating, compile error classification, runtime debug flow
- logs and emissions tracking

### compiler_service.py

Impacts:

- app.py compile path
- lldb_engine.py when it expects temp_program path
- all output handling and runtime status classification
- generated file layout and cleanup expectations

### secure_scan.py

Impacts:

- app.py preflight gate
- fix_engine.validate_ai_fix()
- all user code acceptance logic

### error_classifier.py

Impacts:

- compile-error path in app.py
- AI prompt generation and model behavior
- fix reasoning and likely classification accuracy

### fix_engine.py

Impacts:

- compile-error fix validation path in app.py
- code extraction and post-scan security gate
- output trust boundary

### lldb_engine.py

Impacts:

- runtime error branch in app.py
- debugger execution semantics and security risk
- prompt reliance and JSON parsing

### audit_logger.py

Impacts:

- session recording, persistent history, and JSON file integrity
- any process that expects `system_interactions.json` format

### error_logger.py

Impacts:

- CSV logging reliability and analysis of compile errors
- anything reading compiler_errors.csv

---

## 26. SAFE MODIFICATION GUIDELINES

Changing compiler output handling may require updates to:

- compiler_service.py
- app.py
- error_logger.py
- error_classifier.py
- lldb_engine.py

Changing AI response format may require changes to:

- error_classifier.py
- fix_engine.py
- lldb_engine.py
- app.py

Changing security behavior may require changes to:

- secure_scan.py
- app.py
- compiler_service.py
- lldb_engine.py

Changing file paths or temp file behavior may require changes to:

- compiler_service.py
- lldb_engine.py
- app.py
- .gitignore

Changing logging format may require changes to:

- audit_logger.py
- error_logger.py
- system_interactions.json consumers
- app.py message logging calls

---

## 27. CURRENT TECHNICAL DEBT

| Debt | Evidence | Affected area | Consequence | Possible resolution |
|---|---|---|---|---|
| incomplete dependency declarations | requirements.txt only lists streamlit | setup and environment | installation errors | add all actual imports and pin versions |
| fake security boundary | regex-only scan with no sandbox | security architecture | unsafe execution remains possible | isolate execution in container or VM |
| runtime exit-code bug | any non-negative exit is success | compiler pipeline | wrong status classification | handle exit codes by signal and explicit semantics |
| improper file naming | temp_source.cpp vs temp_code.cpp mismatch | logging | line numbers often Unknown | use one canonical source filename |
| shared-file concurrency | fixed temp_source.cpp and temp_program names | multi-user support | collisions and race conditions | per-session workdir and immutable run IDs |
| AI output not compiled | fix_engine does not compile fix | trust in generated fix | can return broken code | validate by compile/run in sandbox |
| unvalidated LLDB commands | model-specified command executes directly | debugger safety | command execution risk | allowlist commands and schema validation |
| duplicate state setup | repeated session-state init and duplicated LLDB blocks | UI | stale or inconsistent state | remove duplication |
| no real tests | only sample files | maintainability | regressions not caught | establish automated tests |

---

## 28. PROJECT KNOWLEDGE GRAPH

```
USER
  ↓
STREAMLIT UI (app.py)
  ↓
SECURITY SCAN (secure_scan.py)
  ↓
C++ COMPILER (compiler_service.py)
  ├─ success → output display → AI logic review
  ├─ compile error → error_classifier → prompt → Ollama → fix_engine → UI
  └─ runtime error → LLDB agent → Ollama → diagnosis → UI
  ↓
LOGGING
  ├─ JSON chat log (audit_logger.py)
  ├─ CSV compiler errors (error_logger.py)
  └─ CSV emissions (CodeCarbon)
```

---

## 29. AI HANDOFF

### PROJECT:
Chatbot-Driven C++ Compiler Debugger

### PURPOSE:
An educational Streamlit app that lets users paste C++ code, compile it, analyze errors, request AI suggestions, and use a local LLM and LLDB to debug runtime crashes.

### CURRENT ARCHITECTURE:
Single-process Streamlit app orchestrating compile execution, security scan, AI prompting, and LLDB debugging in one Python entry point. Files are mostly coordinated through app.py and direct subprocess calls.

### TECH STACK:
Python, Streamlit, requests, CodeCarbon, g++, LLDB, Ollama, llama3.

### ENTRY POINT:
Chatbot-Driven-Compiler-Debugger/app.py

### CORE FILES:
- app.py
- compiler_service.py
- secure_scan.py
- error_classifier.py
- fix_engine.py
- lldb_engine.py
- audit_logger.py
- error_logger.py

### EXECUTION FLOW:
User code → security scan → compile/run → branch on exit status → AI review/diagnostic/fix → logs and metrics.

### COMPILER:
The actual compiler call is g++ with debug symbols: `g++ -g temp_source.cpp -o temp_program`.

### SECURITY:
Regex-based source scanning only. This is not a real sandbox, container, or restricted execution environment.

### LLM:
Ollama local API at http://localhost:11434/api/generate using model llama3. No explicit timeout or retry logic.

### LLDB:
LLDB is launched by subprocess with model-selected commands. There is no command allowlist.

### LOGGING:
JSON interaction log, CSV compiler errors, CSV emissions metrics.

### STATE:
Streamlit session state is used for editor code, messages, results, agent logs, and metrics.

### DEPENDENCIES:
Actual Python imports include streamlit, requests, codecarbon; requirements.txt is incomplete.

### SYSTEM REQUIREMENTS:
Python, Streamlit, g++, LLDB, Ollama, llama3 model.

### KNOWN BUGS:
- exit codes are mishandled
- temp file names are shared and collisions are possible
- LLDB command execution is not validated
- regex security scan is weak
- no compile validation of AI-generated fixes
- line-number parsing mismatches temp_source.cpp vs temp_code.cpp

### KNOWN LIMITATIONS:
- not a secure sandbox
- not multi-user-safe
- not cross-platform robust
- no automated tests
- AI-generated code is not compiled/executed before display

### TECHNICAL DEBT:
High coupling, fixed names, unvalidated AI output, incomplete dependency list, repeated Streamlit init, lack of strong tests or isolation.

### IMPORTANT FILE RELATIONSHIPS:
- app.py orchestrates the flow and calls all service modules.
- compiler_service.py owns compile/run results.
- secure_scan.py is the frontend security gate.
- error_classifier.py frames the compiler-error prompt.
- fix_engine.py validates the AI-generated fix.
- lldb_engine.py handles runtime-debugging loops.
- audit_logger.py and error_logger.py record history.

### HIGH-RISK AREAS:
- running untrusted C++ code as the current OS user
- raw LLM prompts containing source and compiler output
- LLDB command execution from model-selected text
- fixed temp source/binary names
- no compile-run validation of AI-generated patches

### CURRENT IMPLEMENTATION STATUS:
Functional prototype; educational and local-only; not a secure deployment architecture.

## Rules for another AI modifying this project

1. Inspect existing code before changing it.
2. Preserve existing behavior unless explicitly requested otherwise.
3. Do not invent APIs.
4. Do not invent files.
5. Do not expose secrets.
6. Verify frontend/backend or module contracts.
7. Consider platform compatibility.
8. Consider concurrency.
9. Update tests when behavior changes.
10. Explain which files are affected by a modification.
11. Distinguish confirmed facts from assumptions.
12. Do not perform a large rewrite unless explicitly requested.

---

## 30. FINAL AUDIT REPORT

Files inspected:

- Chatbot-Driven-Compiler-Debugger/app.py
- Chatbot-Driven-Compiler-Debugger/compiler_service.py
- Chatbot-Driven-Compiler-Debugger/secure_scan.py
- Chatbot-Driven-Compiler-Debugger/error_classifier.py
- Chatbot-Driven-Compiler-Debugger/fix_engine.py
- Chatbot-Driven-Compiler-Debugger/lldb_engine.py
- Chatbot-Driven-Compiler-Debugger/audit_logger.py
- Chatbot-Driven-Compiler-Debugger/error_logger.py
- Chatbot-Driven-Compiler-Debugger/README.md
- Chatbot-Driven-Compiler-Debugger/requirements.txt
- Chatbot-Driven-Compiler-Debugger/final_tests.cpp
- Chatbot-Driven-Compiler-Debugger/test1.cpp
- Chatbot-Driven-Compiler-Debugger/verify_cpp.py
- Chatbot-Driven-Compiler-Debugger/compile_check.py
- Chatbot-Driven-Compiler-Debugger/.gitignore
- Chatbot-Driven-Compiler-Debugger/system_interactions.json
- Chatbot-Driven-Compiler-Debugger/compiler_errors.csv
- Chatbot-Driven-Compiler-Debugger/emissions.csv
- Chatbot-Driven-Compiler-Debugger/github_link.txt
- Chatbot-Driven-Compiler-Debugger/github_risk_data.csv
- c:/TOACD_PROJECT/check.cpp
- c:/TOACD_PROJECT/check.exe

Functions inspected:

- get_ai_explanation()
- stop_and_save_metrics()
- reset_execution_state()
- compile_and_run()
- run_security_guardrail()
- classify_error()
- get_diagnostic_prompt()
- validate_ai_fix()
- execute_lldb_command()
- agentic_debug_loop()
- log_interaction()
- parse_gcc_error()
- log_error()

External services:

- Ollama at http://localhost:11434/api/generate
- CodeCarbon library
- g++ compiler process
- lldb debugger process

System dependencies:

- Python
- Streamlit
- g++
- LLDB
- Ollama
- llama3 model

Environment variables:

- No explicit environment-variable usage was found in the inspected code.
- No secrets or hard-coded credentials were discovered in the repo.

Major workflows:

- compile success path
- compile failure path
- runtime crash path
- follow-up chat
- AI fix validation
- LLDB debugging loop
- logging and metrics

Tests found:

- manual sample C++ files
- ad hoc compile helpers
- no automated test suite

Potential bugs:

- exit code classification bug
- fixed temp file collision risk
- LLDB command parsing risk
- regex parsing mismatch for temp_code.cpp vs temp_source.cpp
- unvalidated AI-generated patch flow
- duplicated Streamlit state setup

Security concerns:

- direct execution of untrusted C++ as the current user
- regex-only scan is not a sandbox
- no network/fs/process restrictions
- raw untrusted source and compiler output inserted into prompts
- model-selected LLDB commands execute directly

Platform concerns:

- app appears macOS-oriented and not robustly cross-platform
- `./temp_program` assumptions and Apple Silicon wording are evidence of platform-specific design

### INFORMATION NOT DETERMINABLE FROM THE REPOSITORY

The repository does not contain enough evidence for:

- production deployment configuration
- actual user traffic or concurrency loads
- real external network policies
- real cloud infrastructure or production secrets
- actual Ollama server limits or model runtime settings
- real enterprise security controls used outside the local prototype

Do not guess beyond the code and repository evidence.

---

# AI HANDOFF FILE COMPLETE

This document intentionally records the implementation as it exists in the workspace, and it distinguishes clearly between confirmed facts and inferred conclusions.
