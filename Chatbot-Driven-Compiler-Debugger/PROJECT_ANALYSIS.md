# Chatbot-Driven C++ Compiler Debugger

## 1. Project Overview

Chatbot-Driven Compiler Debugger is a Streamlit web application that helps users debug C++ programs using:

- C++ compilation and execution
- Basic security scanning
- Compiler-error classification
- Local AI explanations and fixes through Ollama and Llama 3
- LLDB-based runtime debugging
- Chat-based follow-up questions
- Error, interaction, and energy-consumption logging

The main entry point is `app.py`.

## 2. Project Structure

```text
Chatbot-Driven-Compiler-Debugger/
|
|-- app.py                    Main Streamlit application and UI
|-- compiler_service.py       C++ compilation and execution
|-- secure_scan.py            Security rule scanner
|-- error_classifier.py       Compiler error categorization
|-- fix_engine.py             Validation of AI-generated fixes
|-- lldb_engine.py            LLDB runtime debugging agent
|-- audit_logger.py           Conversation logging
|-- error_logger.py           Compiler-error CSV logging
|
|-- requirements.txt          Python dependency list
|-- README.md                 Project documentation
|-- .gitignore                Ignored files and build artifacts
|
|-- final_tests.cpp           Four demonstration test cases
|-- test1.cpp                 C++ sorting/compiler test
|
|-- compiler_errors.csv       Historical compiler errors
|-- emissions.csv             CodeCarbon energy measurements
|-- github_risk_data.csv      Security keyword statistics
|-- github_link.txt           GitHub repository URL
|
|-- __pycache__/              Python-generated cache directory
|-- .DS_Store                 macOS metadata file
```

Runtime-generated files include:

```text
temp_source.cpp               Temporary submitted C++ source
temp_program                  Compiled executable
system_interactions.json      AI conversation history
debug_target.cpp              LLDB self-test source
debug_target.bin              LLDB self-test executable
```

## 3. Complete Execution Flow

```text
User enters C++ code
        |
        v
Streamlit application: app.py
        |
        v
Security guardrail scan
        |
        +--> Unsafe: block execution
        |
        +--> Safe: write temp_source.cpp
                              |
                              v
                       Compile with g++
                              |
              +---------------+----------------+
              |                                |
       Compilation error                  Compilation success
              |                                |
              v                                v
       Log and classify error          Run temp_program
              |                                |
              v                    +-----------+-----------+
       Send prompt to Ollama        |                       |
              |                 Normal result          Runtime crash
              v                       |                       |
       Extract AI C++ fix             v                       v
              |                 Display output       Start LLDB agent
              v                       |                       |
       Security-scan fix              v                Ollama selects command
              |                 Ask AI for logic              |
              v                       |                       v
       Display explanation            v                 Run LLDB command
       and corrected code       Save interaction              |
                                                            v
                                                     Display diagnosis

All paths also save metrics and relevant logs.
```

## 4. Main Application: `app.py`

`app.py` controls the complete Streamlit application.

### User interface

The interface contains two main columns.

#### Left column

- C++ code editor
- `Compile & Analyze` button
- Clear-chat button
- Compiler output
- Runtime output
- LLDB debugging trace
- Energy and carbon metrics

#### Right column

- AI tutor conversation
- Previous assistant answers
- Follow-up chat input

### AI configuration

```text
Ollama URL: http://localhost:11434/api/generate
Model:      llama3
```

The application is designed to use a locally running AI model. It does not use a cloud AI API.

### Successful execution path

1. User submits C++ source code.
2. The security scanner checks it.
3. The source is compiled with `g++`.
4. The generated executable is run.
5. Output is displayed.
6. The code is sent to Llama 3 for logical-review feedback.
7. The feedback is added to the chat.
8. The interaction is saved to `system_interactions.json`.
9. CodeCarbon metrics are displayed.

### Compilation-error path

1. Compiler output is collected.
2. The error is logged to `compiler_errors.csv`.
3. The error is classified by `error_classifier.py`.
4. A strict diagnostic prompt is generated.
5. Ollama explains the error and generates corrected C++.
6. The C++ code block is extracted from the response.
7. The generated code is scanned for banned functions.
8. The explanation and safe-looking code are displayed.

### Runtime-error path

1. Runtime output and error text are collected.
2. The LLDB debugging agent is started.
3. Ollama selects an LLDB command such as `bt` or `frame variable`.
4. LLDB executes the command.
5. The LLDB output is returned to Ollama.
6. The agent can perform up to two turns.
7. The final diagnosis is displayed in the UI.

### Follow-up chat

The user can ask questions such as:

```text
What does line 4 mean?
Why is this variable invalid?
How can I prevent this crash?
```

The follow-up prompt includes:

- Current C++ code
- The last four conversation messages
- The latest user question

## 5. C++ Compiler Service

`compiler_service.py` contains `compile_and_run()`.

It performs the following actions:

1. Writes the submitted code to `temp_source.cpp`.
2. Compiles it with debug symbols.
3. Creates the executable `temp_program`.
4. Runs the executable.
5. Returns a status code, standard output, and standard error.

The effective compiler command is:

```text
g++ -g temp_source.cpp -o temp_program
```

Timeouts:

```text
Compilation timeout: 10 seconds
Execution timeout:   5 seconds
```

Intended status values:

```text
0   Successful execution
1   Compilation failure
2   Runtime crash
-1  Timeout or system error
```

Important limitation: normal nonzero program exits are treated as successful executions. A program returning `1`, for example, may still be shown as successful.

## 6. Security Scanner

`secure_scan.py` implements a regex-based denylist.

It detects or blocks examples such as:

- `gets`
- `strcpy`
- `strcat`
- `sprintf`
- `system()`
- `popen()`
- `exec*()`
- Unsafe `printf()` patterns
- `mktemp()`
- `tmpnam()`
- `free()`
- Manual `delete`
- `scanf("%s")`
- `rand()`
- `srand()`
- `void main`
- `bits/stdc++.h`

The function returns:

```python
(is_safe, warning_message)
```

Example result:

```python
False, "CRITICAL: 'system()' allows arbitrary OS command execution..."
```

### Security limitations

This is not a true operating-system sandbox. It only scans source text and applies subprocess timeouts.

The compiled C++ program still runs with the permissions of the Python process. There is no:

- Filesystem isolation
- Network isolation
- Memory limit
- CPU quota
- Container isolation
- Restricted user account
- System-call filtering
- Process privilege separation

The scanner can also produce false positives in comments and strings, while unsafe code can bypass regular-expression rules.

## 7. Error Classifier

`error_classifier.py` classifies compiler messages using keyword matching.

Supported categories:

- `Syntax Error`
- `Linker Error`
- `Type Mismatch`
- `Runtime Error (Infinite Loop)`
- `Security Warning`
- `General Error`

Examples:

```text
"expected" or "missing"        -> Syntax Error
"undefined reference"          -> Linker Error
"cannot convert"               -> Type Mismatch
"infinite loop"                -> Runtime Error
"deprecated" or "unsafe"      -> Security Warning
```

The diagnostic prompt instructs the AI to return:

```markdown
### EXPLANATION

### SECURE FIXED CODE
```cpp
...
```
```

## 8. AI Fix Validation

`fix_engine.py` validates AI responses by:

1. Searching for a Markdown `cpp` code block.
2. Extracting the code.
3. Running the security scanner.
4. Returning the code if it passes the scan.

It does not:

- Recompile the generated code
- Execute the generated code
- Verify semantic correctness
- Check whether original behavior was preserved
- Run automated tests
- Check memory safety

Therefore, the phrase `verified code` currently means only that the code passed the regular-expression security scan.

## 9. LLDB Debugging Agent

`lldb_engine.py` implements the agentic runtime-debugging loop.

The AI receives:

- C++ source code
- Runtime crash output
- LLDB instructions
- Allowed actions

The AI chooses one of:

```text
lldb_command
final_fix
```

LLDB is executed in batch mode using a command similar to:

```text
lldb -b -o run -o <command> <executable>
```

The agent is limited to two turns to prevent an infinite debugging loop.

### LLDB limitations

- LLDB must be installed and available on the PATH.
- The implementation is mainly designed for macOS and Apple Silicon.
- AI-selected LLDB commands are not validated before execution.
- Invalid JSON responses can cause failures.
- The executable path may not behave correctly on Windows.

## 10. Logging

### Interaction logging

`audit_logger.py` writes to:

```text
system_interactions.json
```

Each interaction contains:

```json
{
    "timestamp": "...",
    "error_category": "...",
    "code_snippet": "...",
    "user_prompt": "...",
    "llm_response": "...",
    "fixed_code": "..."
}
```

The log stores user questions, AI responses, source code, error categories, and generated fixes.

### Compiler-error logging

`error_logger.py` writes to:

```text
compiler_errors.csv
```

Columns are:

```text
Timestamp
Error_Type
Line_Number
Raw_Message
Source_Code_Snippet
```

There is a filename mismatch in the current project: the compiler creates `temp_source.cpp`, but the parser searches for errors containing `temp_code.cpp`. As a result, many logged errors may receive an `Unknown` line number.

### Emissions logging

`emissions.csv` contains CodeCarbon measurements, including:

- Duration
- Carbon emissions
- CPU energy
- GPU energy
- RAM energy
- Total energy
- CPU model
- Operating system
- Python version
- Hardware utilization

## 11. Test Files

### `final_tests.cpp`

Contains four demonstration scenarios.

#### Test 1: Security violation

Calls `system()` and should be blocked before compilation.

#### Test 2: Syntax and naming errors

Contains a missing semicolon and uses `totalMarks` instead of `total_marks`.

#### Test 3: Runtime error

Divides by zero and is intended to trigger the LLDB path.

#### Test 4: Logic error

Prints ranks 1 through 6 instead of 1 through 5. It should compile successfully and receive an AI logic review.

### `test1.cpp`

Sorts a vector of `Player` objects without defining a comparison operator. This should produce a C++ compilation error because `std::sort` does not know how to compare `Player` objects.

## 12. Requirements

`requirements.txt` currently contains only:

```text
streamlit
```

However, the Python source also imports:

```text
requests
codecarbon
```

A complete Python installation command is:

```powershell
pip install streamlit requests codecarbon
```

Required external tools:

```text
Python
Streamlit
g++
LLDB
Ollama
Llama 3 model
```

Ollama setup generally requires:

```powershell
ollama serve
ollama pull llama3
```

Start the application with:

```powershell
streamlit run app.py
```

## 13. Windows Compatibility

The project appears to have been developed for macOS and Apple Silicon. Evidence includes:

- Apple Silicon wording in the AI prompts
- macOS-oriented LLDB assumptions
- `/Applications/Calculator.app` in a test
- CodeCarbon records showing Apple M3 hardware
- Unix-style executable paths such as `./temp_program`
- Unix-oriented crash handling

On Windows, the project may require:

- MinGW or LLVM/Clang for the compiler
- LLDB installed separately
- PATH configuration
- Windows-compatible executable handling
- Different runtime-crash detection
- Removal or replacement of macOS-specific commands

The complete compiler and LLDB workflow is not guaranteed to work on Windows without portability changes.

## 14. Important Technical Problems

1. `requests` and `codecarbon` are missing from `requirements.txt`.
2. The timeout layer is called a sandbox, but it is not a secure sandbox.
3. AI-generated fixes are scanned but never compiled or tested.
4. Runtime crash detection is platform-dependent.
5. Division by zero may not be recognized correctly as a runtime crash on Windows.
6. Normal nonzero program exits are treated as successful execution.
7. The compiler creates `temp_source.cpp`, while the error parser expects `temp_code.cpp`.
8. Fixed filenames can cause conflicts between simultaneous users.
9. Temporary source and executable files are not consistently cleaned up.
10. AI-selected LLDB commands are executed without validation.
11. Ollama requests do not specify an HTTP timeout.
12. The Streamlit message state is initialized twice.
13. The LLDB process section is rendered twice.
14. The `.gitignore` entry appears malformed:

```text
system_interactions.jsongithub_scraper.py
```

It probably intended to contain:

```text
system_interactions.json
github_scraper.py
```

15. Generated data and logs are stored inside the main project directory.

## 15. Overall Architecture

```text
Streamlit UI
    |
    v
Security Scanner
    |
    v
C++ Compiler and Runner
    |
    +--> Success --> Output + AI Logic Review
    |
    +--> Compile Error --> Classifier --> Ollama Fix --> Security Scan
    |
    +--> Runtime Crash --> Ollama Agent --> LLDB --> Diagnosis
    |
    v
Logging and Carbon Metrics
```

## 16. Final Summary

This project is an educational AI-assisted C++ debugging tool. Its main strengths are:

- Integrated compiler and debugger workflow
- Local Llama 3 integration
- Automated compiler-error explanations
- AI-generated correction suggestions
- LLDB experimentation
- Conversation and error logging
- Energy and carbon metrics

Its most important weakness is security. The current implementation should not be used to execute untrusted C++ code in production because the source scanner and timeout mechanism do not provide real process isolation.
