"""
secure_scan.py - Static Pre-Execution Security Scanner and Guardrail Engine
=============================================================================
Inspects C++ source code for malicious constructs, dangerous OS calls, network
exfiltration, path traversal, shellcode, and memory safety vulnerabilities
WITHOUT executing the code.
"""

import os
import re
from urllib.parse import urlsplit

# Comprehensive threat patterns matrix
# Note: Existing keys and message prefixes (CRITICAL, WARNING, PERFORMANCE, STANDARD, RISK)
# are preserved for 100% backward compatibility with diagnostic and testing engines.
SECURITY_PATTERNS = {
    # --- 1. Memory Safety & Deprecated Insecure C Primitives ---
    r'\bgets\b': "CRITICAL: 'gets()' performs no bounds checking. It is officially removed from C++14. Use 'std::cin' or 'fgets()'.",
    r'\bstrcpy\b': "CRITICAL: 'strcpy' does not check buffer size. Use 'strncpy' or 'std::string'.",
    r'\bstrcat\b': "CRITICAL: 'strcat' can write past the end of a buffer. Use 'strncat' or 'std::string::append'.",
    r'\bsprintf\b': "CRITICAL: 'sprintf' lacks bounds checking. Use 'snprintf' to prevent overflows.",
    r'\bvsprintf\b': "CRITICAL: 'vsprintf' is unsafe. Use 'vsnprintf'.",
    r'\bwcscpy\b': "CRITICAL: Wide-character version of strcpy is equally unsafe. Use 'wcsncpy'.",
    r'\bwcscat\b': "CRITICAL: Wide-character version of strcat is unsafe. Use 'wcsncat'.",
    r'\bstrncpy\b': "WARNING: 'strncpy' can leave strings unterminated if the source is larger than the destination. Prefer 'std::string'.",
    
    # --- 2. Host OS Command Execution & Process Spawning ---
    r'\bsystem\s*\(': "CRITICAL: 'system()' allows arbitrary OS command execution. This is a severe injection vulnerability.",
    r'\bpopen\s*\(': "CRITICAL: 'popen()' opens a pipe to a shell command, which is vulnerable to injection attacks.",
    r'\b(eval|exec)\s*\(': "CRITICAL: Arbitrary dynamic code evaluation ('eval'/'exec') detected. Unsafe code injection risk.",
    r'\b(subprocess\.(call|Popen|run|check_output)|os\.system|os\.popen|os\.spawn)\b': "CRITICAL: Host OS command execution via subprocess or OS API detected.",
    r"\b(?:require\s*\(\s*['\"](?:node:)?child_process['\"]\s*\)|from\s+['\"](?:node:)?child_process['\"]|\bchild_process\s*\.\s*(?:exec|execSync|spawn|spawnSync|fork|execFile|execFileSync)\s*\()":
        "CRITICAL: Node.js child-process execution is blocked.",
    r'\bnew\s+Function\s*\(': "CRITICAL: Dynamic code construction is blocked.",
    r'\b(?:requests|urllib\.request)\s*\.\s*(?:get|post|put|patch|delete|urlopen)\s*\(': "CRITICAL: Python network access is blocked.",
    r'\b(?:fetch|axios\.(?:get|post|put|patch|delete)|https?\.request)\s*\(': "CRITICAL: JavaScript network access is blocked.",
    r'\b(?:Deno\.Command|Bun\.(?:spawn|spawnSync))\s*\(': "CRITICAL: Runtime command execution is blocked.",
    r'\b(shutil\.rmtree|os\.remove|os\.unlink)\s*\(': "CRITICAL: Filesystem deletion API detected.",
    r'\bexecl\s*\(': "CRITICAL: The 'execl' function poses command injection risks. Validate all inputs before execution.",
    r'\bexecle\s*\(': "CRITICAL: The 'execle' function poses command injection risks.",
    r'\bexeclp\s*\(': "CRITICAL: The 'execlp' function poses command injection risks.",
    r'\bexecv\s*\(': "CRITICAL: The 'execv' function poses command injection risks.",
    r'\bexecvp\s*\(': "CRITICAL: The 'execvp' function poses command injection risks.",
    r'\bexecve\s*\(': "CRITICAL: The 'execve' function poses command injection risks.",
    r'\b(fork|vfork|clone)\s*\(': "CRITICAL: Process forking/cloning detected. Spawning uncontrolled processes or fork bombs is prohibited.",
    r'\b(CreateProcess[AW]?|WinExec|ShellExecute[AW]?|ShellExecuteEx[AW]?)\s*\(': "CRITICAL: Windows process execution API detected. Direct OS process spawning is blocked.",
    r'\b(CreateRemoteThread|OpenProcess|TerminateProcess|ExitWindowsEx)\s*\(': "CRITICAL: Low-level Windows process/session manipulation API detected.",

    # --- 3. Format String Vulnerabilities ---
    r'\bprintf\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)': "CRITICAL: Format string vulnerability. Never pass a variable directly to 'printf'. Use 'printf(\"%s\", var)'.",
    r'\bfprintf\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*,\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)': "CRITICAL: Format string vulnerability in 'fprintf'. Ensure the format string is hardcoded.",
    r'\bsyslog\s*\(': "WARNING: Ensure you are not passing un-sanitized user input directly to 'syslog()'.",

    # --- 4. Network Infiltration & Data Exfiltration ---
    r'#include\s*<((sys/socket|winsock2|ws2tcpip|wininet|curl/curl)\.h)>': "CRITICAL: Network header inclusion detected. Socket creation and network operations are disabled for security.",
    r'\b(WSAStartup|WSACleanup)\s*\(': "CRITICAL: Windows Sockets initialization API detected. Networking is prohibited.",
    r'(?<!sqlite3\.)(?<!sqlite\.)(?<!db\.)(?<!engine\.)(?<!pymysql\.)(?<!psycopg2\.)\b(socket|connect|bind|listen|accept)\s*\(': "CRITICAL: Socket communication function detected. Network connections are prohibited.",
    r'\b(InternetOpen[AW]?|InternetOpenUrl[AW]?|URLDownloadToFile[AW]?|HttpOpenRequest[AW]?)\s*\(': "CRITICAL: Network exfiltration or web download API detected.",

    # --- 5. Filesystem Tampering & Path Traversal ---
    r'(\.\./|\.\.\\)': "CRITICAL: Path traversal ('../' or '..\\') detected. Access outside the execution sandbox is prohibited.",
    r'(?i)["\'][a-z]:[/\\]windows': "CRITICAL: Attempted access to Windows system directory detected.",
    r'(?i)["\'][/\\]etc[/\\](passwd|shadow)': "CRITICAL: Attempted access to system credential files detected.",
    r'["\']\.env["\']': "CRITICAL: Attempted access to application secret/environment file (.env) detected.",
    r'(\\\\\\\\.(\\\\|/)|//\./)': "CRITICAL: Low-level hardware device or drive manipulation path detected.",
    r'\b(DeleteFile[AW]?|remove|unlink|rmdir)\s*\(': "CRITICAL: File or directory deletion API detected. Arbitrary file destruction is prohibited.",

    # --- 6. Dynamic Evasion & DLL Loading ---
    r'\b(LoadLibrary[AW]?|LoadLibraryEx[AW]?|GetProcAddress)\s*\(': "CRITICAL: Dynamic library loading detected. Resolving APIs dynamically bypasses static compiler security.",
    r'\b(dlopen|dlsym)\s*\(': "CRITICAL: Dynamic module loading (dlopen/dlsym) is prohibited in the sandbox.",

    # --- 7. Inline Assembly & Raw Shellcode Execution ---
    r'\b(__asm__|__asm|asm)\b': "CRITICAL: Inline assembly instructions detected. Direct CPU instructions bypass compiler safety constraints.",
    r'\b(VirtualAlloc|VirtualProtect|VirtualAllocEx)\s*\(': "CRITICAL: Low-level virtual memory allocation with execution permissions is prohibited.",
    r'\bmprotect\s*\(': "CRITICAL: Memory page permission modification (mprotect) is prohibited.",

    # --- 8. Windows Registry & System Hooks ---
    r'\b(RegOpenKey[AW]?|RegSetValue[AW]?|RegCreateKey[AW]?|RegDeleteKey[AW]?)\s*\(': "CRITICAL: Modifying or reading Windows Registry keys is prohibited.",
    r'\bSetWindowsHookEx[AW]?\s*\(': "CRITICAL: Installing Windows global hooks is prohibited.",

    # --- 9. Macro Aliasing of Prohibited Primitives ---
    r'#\s*define\s+\w+\s+(system|popen|CreateProcess|WinExec|fork|execl|execv|socket|VirtualAlloc)\b': "CRITICAL: Macro aliasing of prohibited security function detected.",

    # --- 10. Compiler Denial-of-Service & Device Hanging ---
    r'#\s*include\s*["<](CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])[" >]': "CRITICAL: Inclusion of reserved device name hangs compiler (Denial of Service).",
    r'#\s*include\s+__FILE__': "CRITICAL: Recursive self-inclusion (__FILE__) triggers preprocessor denial-of-service.",

    # --- 11. Legacy Temporary Files & Deprecated Features ---
    r'\bmktemp\s*\(': "CRITICAL: 'mktemp' suffers from race conditions. Use 'mkstemp' instead.",
    r'\btmpnam\s*\(': "CRITICAL: 'tmpnam' is obsolete and insecure. Use 'mkstemp'.",
    r'\btempnam\s*\(': "CRITICAL: 'tempnam' is vulnerable to race conditions. Use 'mkstemp'.",
    r'\bauto_ptr\b': "CRITICAL: 'std::auto_ptr' is deprecated in C++11 and removed in C++17 due to unsafe copy semantics. Use 'std::unique_ptr'.",
    r'\bfree\s*\(': "WARNING: Mixing 'malloc/free' with 'new/delete' causes undefined behavior. Stick to C++ paradigms (new/delete or smart pointers).",
    r'\bdelete\s+': "WARNING: Manual 'delete' can lead to dangling pointers. Modern C++ heavily favors 'std::unique_ptr' or 'std::shared_ptr'.",
    r'\bscanf\s*\(\s*\"%s\"': "RISK: 'scanf' with '%s' has no bounds checking. Specify a width (e.g., '%49s') or use 'std::cin'.",
    r'\brand\s*\(\s*\)': "WARNING: 'rand()' is not cryptographically secure. For security-sensitive randomness, use the '<random>' library (e.g., std::mt19937).",
    r'\bsrand\s*\(': "WARNING: Seeding with 'srand(time(NULL))' is predictable. Use a true hardware entropy source like 'std::random_device'.",
    r'system\s*\(\s*\"pause\"\s*\)': "WARNING: 'system(\"pause\")' is platform-dependent (Windows only). Use 'std::cin.get()' for cross-platform compatibility.",
    r'\bvoid\s+main\b': "STANDARD: 'void main()' is non-standard C++. The standard dictates using 'int main()' and returning 0.",
    r'#include\s+<bits/stdc\+\+\.h>': "PERFORMANCE: Including '<bits/stdc++.h>' drastically increases compilation time and makes code non-portable. Include specific headers."
}


# Host execution threats that MUST block execution to protect the host environment
HOST_EXECUTION_BLOCKLIST = {
    # --- 2. Host OS Command Execution & Process Spawning ---
    r'\bsystem\s*\(': "CRITICAL: 'system()' allows arbitrary OS command execution. This is a severe injection vulnerability.",
    r'\bpopen\s*\(': "CRITICAL: 'popen()' opens a pipe to a shell command, which is vulnerable to injection attacks.",
    r'\b(eval|exec)\s*\(': "CRITICAL: Arbitrary dynamic code evaluation ('eval'/'exec') detected. Unsafe code injection risk.",
    r'\b(subprocess\.(call|Popen|run|check_output)|os\.system|os\.popen|os\.spawn)\b': "CRITICAL: Host OS command execution via subprocess or OS API detected.",
    r"\b(?:require\s*\(\s*['\"](?:node:)?child_process['\"]\s*\)|from\s+['\"](?:node:)?child_process['\"]|\bchild_process\s*\.\s*(?:exec|execSync|spawn|spawnSync|fork|execFile|execFileSync)\s*\()":
        "CRITICAL: Node.js child-process execution is blocked.",
    r'\bnew\s+Function\s*\(': "CRITICAL: Dynamic code construction is blocked.",
    r'\b(?:requests|urllib\.request)\s*\.\s*(?:get|post|put|patch|delete|urlopen)\s*\(': "CRITICAL: Python network access is blocked.",
    r'\b(?:fetch|axios\.(?:get|post|put|patch|delete)|https?\.request)\s*\(': "CRITICAL: JavaScript network access is blocked.",
    r'\b(?:Deno\.Command|Bun\.(?:spawn|spawnSync))\s*\(': "CRITICAL: Runtime command execution is blocked.",
    r'\b(shutil\.rmtree|os\.remove|os\.unlink)\s*\(': "CRITICAL: Filesystem deletion API detected.",
    r'\bexecl\s*\(': "CRITICAL: The 'execl' function poses command injection risks. Validate all inputs before execution.",
    r'\bexecle\s*\(': "CRITICAL: The 'execle' function poses command injection risks.",
    r'\bexeclp\s*\(': "CRITICAL: The 'execlp' function poses command injection risks.",
    r'\bexecv\s*\(': "CRITICAL: The 'execv' function poses command injection risks.",
    r'\bexecvp\s*\(': "CRITICAL: The 'execvp' function poses command injection risks.",
    r'\bexecve\s*\(': "CRITICAL: The 'execve' function poses command injection risks.",
    r'\b(fork|vfork|clone)\s*\(': "CRITICAL: Process forking/cloning detected. Spawning uncontrolled processes or fork bombs is prohibited.",
    r'\b(CreateProcess[AW]?|WinExec|ShellExecute[AW]?|ShellExecuteEx[AW]?)\s*\(': "CRITICAL: Windows process execution API detected. Direct OS process spawning is blocked.",
    r'\b(CreateRemoteThread|OpenProcess|TerminateProcess|ExitWindowsEx)\s*\(': "CRITICAL: Low-level Windows process/session manipulation API detected.",

    # --- 4. Network Infiltration & Data Exfiltration ---
    r'#include\s*<((sys/socket|winsock2|ws2tcpip|wininet|curl/curl)\.h)>': "CRITICAL: Network header inclusion detected. Socket creation and network operations are disabled for security.",
    r'\b(WSAStartup|WSACleanup)\s*\(': "CRITICAL: Windows Sockets initialization API detected. Networking is prohibited.",
    r'(?<!sqlite3\.)(?<!sqlite\.)(?<!db\.)(?<!engine\.)(?<!pymysql\.)(?<!psycopg2\.)\b(socket|connect|bind|listen|accept)\s*\(': "CRITICAL: Socket communication function detected. Network connections are prohibited.",
    r'\b(InternetOpen[AW]?|InternetOpenUrl[AW]?|URLDownloadToFile[AW]?|HttpOpenRequest[AW]?)\s*\(': "CRITICAL: Network exfiltration or web download API detected.",

    # --- 5. Filesystem Tampering & Path Traversal ---
    r'(\.\./|\.\.\\)': "CRITICAL: Path traversal ('../' or '..\\') detected. Access outside the execution sandbox is prohibited.",
    r'(?i)["\'][a-z]:[/\\]windows': "CRITICAL: Attempted access to Windows system directory detected.",
    r'(?i)["\'][/\\]etc[/\\](passwd|shadow)': "CRITICAL: Attempted access to system credential files detected.",
    r'["\']\.env["\']': "CRITICAL: Attempted access to application secret/environment file (.env) detected.",
    r'(\\\\\\\\.(\\\\|/)|//\./)': "CRITICAL: Low-level hardware device or drive manipulation path detected.",
    r'\b(DeleteFile[AW]?|remove|unlink|rmdir)\s*\(': "CRITICAL: File or directory deletion API detected. Arbitrary file destruction is prohibited.",

    # --- 6. Dynamic Evasion & DLL Loading ---
    r'\b(LoadLibrary[AW]?|LoadLibraryEx[AW]?|GetProcAddress)\s*\(': "CRITICAL: Dynamic library loading detected. Resolving APIs dynamically bypasses static compiler security.",
    r'\b(dlopen|dlsym)\s*\(': "CRITICAL: Dynamic module loading (dlopen/dlsym) is prohibited in the sandbox.",

    # --- 7. Inline Assembly & Raw Shellcode Execution ---
    r'\b(__asm__|__asm|asm)\b': "CRITICAL: Inline assembly instructions detected. Direct CPU instructions bypass compiler safety constraints.",
    r'\b(VirtualAlloc|VirtualProtect|VirtualAllocEx)\s*\(': "CRITICAL: Low-level virtual memory allocation with execution permissions is prohibited.",
    r'\bmprotect\s*\(': "CRITICAL: Memory page permission modification (mprotect) is prohibited.",

    # --- 8. Windows Registry & System Hooks ---
    r'\b(RegOpenKey[AW]?|RegSetValue[AW]?|RegCreateKey[AW]?|RegDeleteKey[AW]?)\s*\(': "CRITICAL: Modifying or reading Windows Registry keys is prohibited.",
    r'\bSetWindowsHookEx[AW]?\s*\(': "CRITICAL: Installing Windows global hooks is prohibited.",

    # --- 9. Macro Aliasing of Prohibited Primitives ---
    r'#\s*define\s+\w+\s+(system|popen|CreateProcess|WinExec|fork|execl|execv|socket|VirtualAlloc)\b': "CRITICAL: Macro aliasing of prohibited security function detected.",

    # --- 10. Compiler Denial-of-Service & Device Hanging ---
    r'#\s*include\s*["<](CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])[" >]': "CRITICAL: Inclusion of reserved device name hangs compiler (Denial of Service).",
    r'#\s*include\s+__FILE__': "CRITICAL: Recursive self-inclusion (__FILE__) triggers preprocessor denial-of-service.",
}

MODEL_INPUT_BLOCKLIST = {
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----":
        "A private key was detected in the source; remove it before sending code to the model.",
    r"\bAKIA[0-9A-Z]{16}\b":
        "An AWS access key was detected in the source; remove it before sending code to the model.",
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b":
        "A GitHub token was detected in the source; remove it before sending code to the model.",
    r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\b\s*[:=]\s*(['\"])[^'\"\r\n]{8,}\1":
        "A likely credential assignment was detected; remove it before sending code to the model.",
}


def run_security_guardrail(code_snippet):
    """
    Scans code for host-threatening patterns (OS commands, network, filesystem,
    evasion, shellcode) and returns (is_safe, warning_message).
    Only hard host-execution threats block compilation/execution.
    Advisory security vulnerabilities (like strcpy or gets) are reported via
    scan_all_security_issues() for static diagnostics without aborting compilation.
    """
    for pattern, warning in HOST_EXECUTION_BLOCKLIST.items():
        if re.search(pattern, code_snippet):
            return False, warning

    return True, "Code looks safe."


MAX_MODEL_PROMPT_BYTES = int(os.environ.get("MAX_MODEL_PROMPT_BYTES", 2 * 1024 * 1024))  # 2 MB default (supports 10,000+ lines)


def validate_model_input(prompt: str) -> tuple[bool, str]:
    """Block model prompts containing dangerous code patterns or recognizable secrets."""
    if len(prompt.encode("utf-8")) > MAX_MODEL_PROMPT_BYTES:
        limit_mb = MAX_MODEL_PROMPT_BYTES // (1024 * 1024)
        return False, f"Model prompt exceeds the {limit_mb} MB safety limit."
    is_safe, warning = run_security_guardrail(prompt)
    if not is_safe:
        return False, warning
    for pattern, message in MODEL_INPUT_BLOCKLIST.items():
        if re.search(pattern, prompt):
            return False, message
    return True, "Model input looks safe."


def validate_model_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("OLLAMA_HOST must be a base HTTP or HTTPS URL without credentials or a path.")
    if parsed.scheme != "https" and parsed.hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError(
            "Code will not be sent to a remote Ollama endpoint over HTTP. "
            "Use HTTPS or a localhost endpoint."
        )


def scan_all_security_issues(code_snippet):
    """
    Scans code against all security patterns and returns all matching issues.
    Used for live diagnostics and editor visual reporting.
    """
    issues = []
    for pattern, warning in SECURITY_PATTERNS.items():
        match = re.search(pattern, code_snippet)
        if match:
            issues.append({"pattern": pattern, "message": warning, "match_span": match.span()})
    return issues


def get_security_summary(code_snippet):
    """
    Returns a structured security audit summary dictionary for telemetry and UI badges.
    """
    all_issues = scan_all_security_issues(code_snippet)
    critical_count = sum(1 for item in all_issues if item["message"].startswith("CRITICAL"))
    warning_count = sum(1 for item in all_issues if item["message"].startswith("WARNING"))
    risk_count = sum(1 for item in all_issues if item["message"].startswith("RISK"))
    
    is_safe = critical_count == 0 and warning_count == 0 and risk_count == 0

    return {
        "is_safe": is_safe,
        "total_threats": len(all_issues),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "risk_level": "BLOCKED" if critical_count > 0 else ("WARNING" if warning_count > 0 else "CLEAN"),
        "issues": all_issues
    }


EFFICIENCY_CHECKS_BY_LANGUAGE = {
    "python": (
        "nested_loop_depth",
        "string_concatenation_in_loop",
        "bare_except",
        "unmemoized_recursion",
    ),
    "sql": ("nested_selects", "unbounded_select_star", "cartesian_product"),
    "cpp": (
        "nested_loop_depth",
        "endl_in_loop",
        "container_parameter_by_value",
        "quadratic_sort",
        "sort_inside_loop",
        "slow_cin_io",
        "unmemoized_recursion",
    ),
    "other": ("nested_loop_depth",),
}


def compute_code_quality_metrics(code_snippet: str, language: str = "cpp") -> dict:
    """
    Reports the fraction of configured static checks that did not flag the source.
    These are scan results, not probabilities or measurements of overall security
    or runtime efficiency.
    Deductions include a percentage-point impact based on the share of checks that
    the finding flagged.
    """
    lines = [line.strip() for line in code_snippet.splitlines() if line.strip() and not line.strip().startswith(("//", "#", "/*", "*", "--"))]
    total_loc = max(1, len(lines))

    deductions = []

    # 1. Security Analysis (Logical Threat Pattern Matching)
    sec_issues = scan_all_security_issues(code_snippet)
    critical_count = 0
    warning_count = 0
    risk_count = 0

    for issue in sec_issues:
        msg = issue.get("message", "")
        line_num = issue.get("line")
        line_str = f"Line {line_num}: " if line_num else ""

        if msg.startswith("CRITICAL"):
            critical_count += 1
            deductions.append({
                "category": "Security",
                "severity": "CRITICAL",
                "title": f"Critical Security Threat ({line_str}{msg.split(':')[0]})",
                "why_deducted": msg,
                "how_to_reach_100": "Remove dynamic shell execution, buffer-overflow primitives, or network calls. Use sandboxed memory-safe constructs.",
                "code_fix": "// Replace unsafe call with standard memory-safe logic"
            })
        elif msg.startswith("WARNING"):
            warning_count += 1
            deductions.append({
                "category": "Security",
                "severity": "WARNING",
                "title": f"Security Advisory ({line_str}Risky API)",
                "why_deducted": msg,
                "how_to_reach_100": "Modernize the code by replacing deprecated C-style library functions with standard C++ / safe language abstractions.",
                "code_fix": "// Modern standard alternative recommended in warning message"
            })
        elif msg.startswith("RISK"):
            risk_count += 1
            deductions.append({
                "category": "Security",
                "severity": "RISK",
                "title": f"Security Risk ({line_str}Unbounded Input)",
                "why_deducted": msg,
                "how_to_reach_100": "Specify explicit buffer bounds or use safe stream extractors with input validation.",
                "code_fix": "cin >> setw(buffer_size) >> buffer;"
            })

    security_checks_total = len(SECURITY_PATTERNS)
    security_checks_flagged = len(sec_issues)
    security_score = (
        100.0 * (security_checks_total - security_checks_flagged) / security_checks_total
        if security_checks_total
        else 100.0
    )
    insecure_pct = 100.0 - security_score
    security_risk_weight = (
        critical_count * 35.0 + warning_count * 15.0 + risk_count * 5.0
    )

    # 2. Multi-Language Algorithmic Efficiency Analysis
    inefficiencies = []
    lang_lower = (language or "").lower()
    is_cpp = lang_lower in {"c", "cpp", "c++", "cxx"}
    if "py" in lang_lower:
        efficiency_profile = "python"
    elif "sql" in lang_lower:
        efficiency_profile = "sql"
    elif is_cpp:
        efficiency_profile = "cpp"
    else:
        efficiency_profile = "other"
    efficiency_checks_total = len(EFFICIENCY_CHECKS_BY_LANGUAGE[efficiency_profile])

    # Detect true loop nesting depth across Python, C++, JavaScript, TypeScript, SQL
    loop_nesting = 0
    if "py" in lang_lower:
        # Check Python loop indentation nesting
        lines = code_snippet.splitlines()
        loop_indents: list[int] = []
        max_py_nest = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("for ") or stripped.startswith("while "):
                indent = len(line) - len(line.lstrip())
                loop_indents = [i for i in loop_indents if i < indent]
                loop_indents.append(indent)
                max_py_nest = max(max_py_nest, len(loop_indents))
        loop_nesting = max_py_nest
    elif "sql" in lang_lower:
        nested_selects = len(re.findall(r'\bSELECT\b', code_snippet, re.IGNORECASE))
        if nested_selects > 1:
            loop_nesting = nested_selects - 1
    else:
        try:
            from complexity_engine import _extract_loops_with_bodies, _calculate_max_loop_nesting
            extracted = _extract_loops_with_bodies(code_snippet)
            loop_nesting = _calculate_max_loop_nesting(extracted)
        except Exception:
            c_loops = len(re.findall(r'\b(for|while)\s*\(', code_snippet))
            loop_nesting = min(c_loops, 2) if c_loops >= 2 else c_loops

    if loop_nesting >= 3:
        note = "Deeply nested loops detected (potential O(n³) or higher complexity)"
        inefficiencies.append(note)
        deductions.append({
            "category": "Efficiency",
            "severity": "WARNING",
            "title": "Deeply Nested Loops (O(n³) Complexity)",
            "why_deducted": "Triple-nested loops scale cubically with data size. At n=1,000 items, O(n³) performs 1,000,000,000 operations, triggering severe execution timeouts.",
            "how_to_reach_100": "Restructure data using hash maps (unordered_map / dict), lookup tables, or prefix arrays to eliminate the innermost iteration loops.",
            "code_fix": "// Refactor inner loop to O(1) hash map lookup: unordered_map<int, int> lookup;"
        })
    elif loop_nesting == 2:
        note = "Nested loops detected (potential O(n²) complexity)"
        inefficiencies.append(note)
        deductions.append({
            "category": "Efficiency",
            "severity": "WARNING",
            "title": "Nested Loops (O(n²) Complexity)",
            "why_deducted": "Nested loops examine pairs of elements (quadratic time). On inputs larger than 10,000 items, this causes noticeable latency degradation.",
            "how_to_reach_100": "If searching or matching elements, pre-index items in a hash table or sort the collection and use two pointers to reach O(n log n) or O(n).",
            "code_fix": "// Example: Use two-pointer approach or hash set instead of nested loop"
        })

    # Language-specific heuristics
    if "py" in lang_lower:
        # String concatenation inside loops
        if loop_nesting > 0 and re.search(r'(\+=|\+)\s*["\']', code_snippet):
            note = "Repeated string concatenation inside loop (creates intermediate objects; prefer str.join())"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "String Concatenation in Loop",
                "why_deducted": "In Python, strings are immutable. Doing '+=' inside a loop allocates memory and copies the entire string on every iteration.",
                "how_to_reach_100": "Collect string chunks into a list inside the loop and combine them at the end with ''.join(parts).",
                "code_fix": "parts = []\nfor item in items:\n    parts.append(str(item))\nresult = ''.join(parts)"
            })
        # Bare except
        if re.search(r'^\s*except\s*:', code_snippet, re.MULTILINE):
            note = "Bare 'except:' catches KeyboardInterrupt & SystemExit (specify exception classes)"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Bare 'except:' Exception Handler",
                "why_deducted": "A bare 'except:' catches all exceptions indiscriminately, suppressing KeyboardInterrupt (Ctrl+C), SystemExit, and masking typos.",
                "how_to_reach_100": "Catch specific exceptions or 'except Exception as e:' to permit runtime control signals to propagate.",
                "code_fix": "except Exception as e:\n    print(f'Error: {e}')"
            })
        # Python recursion without lru_cache
        py_funcs = re.findall(r'^\s*def\s+([a-zA-Z_]\w*)\s*\(', code_snippet, re.MULTILINE)
        for fn in py_funcs:
            calls = len(re.findall(rf'\b{fn}\s*\(', code_snippet))
            if calls > 1 and "cache" not in code_snippet and "memo" not in code_snippet:
                note = f"Recursive Python function '{fn}' without memoization or @lru_cache"
                inefficiencies.append(note)
                deductions.append({
                    "category": "Efficiency",
                    "severity": "WARNING",
                    "title": f"Unmemoized Recursion in '{fn}'",
                    "why_deducted": f"Recursive function '{fn}' calls itself repeatedly without caching overlapping subproblems, triggering exponential O(2ⁿ) execution.",
                    "how_to_reach_100": "Import functools and decorate the function with @functools.lru_cache(None) or maintain a memo dictionary.",
                    "code_fix": "from functools import lru_cache\n\n@lru_cache(maxsize=None)\ndef " + fn + "(...):"
                })
                break
    elif "sql" in lang_lower:
        # Unbounded SELECT * without LIMIT
        if re.search(r'\bSELECT\s+\*\s+FROM\b', code_snippet, re.IGNORECASE) and not re.search(r'\bLIMIT\b', code_snippet, re.IGNORECASE):
            note = "Unbounded 'SELECT *' without LIMIT (may trigger full table scans on large tables)"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Unbounded 'SELECT *' Query",
                "why_deducted": "SELECT * queries without a LIMIT clause force database engines to scan every row and return unneeded columns across the network.",
                "how_to_reach_100": "Specify only the explicit columns needed and add a LIMIT clause.",
                "code_fix": "SELECT id, name FROM users LIMIT 100;"
            })
        # Cartesian join
        if re.search(r'\bFROM\s+[a-zA-Z_]\w*\s*,\s*[a-zA-Z_]\w*', code_snippet, re.IGNORECASE):
            note = "Implicit cross join / Cartesian product detected in FROM clause"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Cartesian Product / Implicit Cross Join",
                "why_deducted": "Specifying multiple tables separated by commas without an explicit ON condition calculates the full mathematical Cartesian product (N × M rows).",
                "how_to_reach_100": "Replace comma separation with an explicit INNER JOIN ... ON tableA.id = tableB.ref_id clause.",
                "code_fix": "FROM table_a INNER JOIN table_b ON table_a.user_id = table_b.id"
            })
    elif is_cpp:
        # C++ / C optimizations
        if re.search(r'\bendl\b', code_snippet) and loop_nesting > 0:
            note = "std::endl used inside loop (flushes output buffer each iteration; prefer '\\n')"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "std::endl Output Buffer Flushing in Loop",
                "why_deducted": "std::endl prints a newline AND executes an immediate OS stream flush (fflush). Inside a loop, this creates massive I/O stall cycles.",
                "how_to_reach_100": "Use '\\n' instead of endl to allow the OS stream buffer to batch writes efficiently.",
                "code_fix": "cout << x << '\\n';"
            })

        if re.search(r'\b(vector|string|map|set)\s+[a-zA-Z0-9_]+\s*[,)]', code_snippet):
            note = "Container parameter passed by value (incurs copy overhead; prefer const reference)"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Container Parameter Passed by Value",
                "why_deducted": "Passing STL containers (e.g. vector, string) by value triggers a full heap copy on each function call.",
                "how_to_reach_100": "Pass heavy containers by const reference ('const vector<T>&') to enable zero-copy reads.",
                "code_fix": "void process(const std::vector<int>& data)"
            })

        # Quadratic Sorting check (Bubble / Selection Sort)
        if re.search(r'\bswap\s*\(\s*[a-zA-Z_]\w*\[[^\]]+\]\s*,\s*[a-zA-Z_]\w*\[[^\]]+\]\s*\)', code_snippet) and loop_nesting > 1:
            note = "Quadratic sorting detected (nested loop with element swaps; prefer std::sort)"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Quadratic Sorting Algorithm (O(N²) Bubble/Selection Sort)",
                "why_deducted": "Nested loops swapping adjacent or indexed array elements run in O(N²) quadratic time, causing severe CPU degradation on large arrays.",
                "how_to_reach_100": "Replace manual nested sorting loops with C++ std::sort() from <algorithm>, which runs in optimal O(N log N) time.",
                "code_fix": "#include <algorithm>\n// Replace nested loops with:\nsort(arr.begin(), arr.end());"
            })

        # Redundant sort inside loop
        if re.search(r'\b(sort|std::sort)\s*\(', code_snippet) and loop_nesting > 0:
            from complexity_engine import analyze_algorithmic_complexity
            comp = analyze_algorithmic_complexity(code_snippet, language="cpp")
            if "N²" in comp.time_complexity or "sort inside loop" in str(comp.hotspots):
                note = "Redundant sort inside loop (causes O(N² log N) complexity; hoist sort outside)"
                inefficiencies.append(note)
                deductions.append({
                    "category": "Efficiency",
                    "severity": "WARNING",
                    "title": "Redundant Sorting Inside Iteration Loop (O(N² log N))",
                    "why_deducted": "Calling sort() inside a loop repeatedly re-sorts already ordered data, ballooning complexity from O(N log N) to O(N² log N).",
                    "how_to_reach_100": "Move sort() immediately before the loop so the dataset is sorted only once.",
                    "code_fix": "sort(arr.begin(), arr.end());\nfor (int i = 0; i < n; i++) { ... }"
                })

        if "cin" in code_snippet and "ios_base::sync_with_stdio" not in code_snippet:
            note = "Unbuffered standard I/O (missing ios_base::sync_with_stdio(false))"
            inefficiencies.append(note)
            deductions.append({
                "category": "Efficiency",
                "severity": "WARNING",
                "title": "Unbuffered Standard I/O (C++ Stream Sync Overhead)",
                "why_deducted": "By default, C++ std::cin synchronizes with C stdio streams (scanf/printf) after every character, adding significant CPU overhead on large inputs.",
                "how_to_reach_100": "Add fast I/O optimization at the start of your main() function to untie C++ streams from C stdio.",
                "code_fix": "ios_base::sync_with_stdio(false);\ncin.tie(NULL);"
            })

        funcs = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{', code_snippet)
        for f in funcs:
            if f not in ("main", "if", "for", "while", "switch"):
                pattern = rf'\b{f}\s*\('
                if len(re.findall(pattern, code_snippet)) > 1 and "memo" not in code_snippet.lower() and "dp" not in code_snippet.lower():
                    note = f"Recursive function '{f}' without apparent memoization"
                    inefficiencies.append(note)
                    deductions.append({
                        "category": "Efficiency",
                        "severity": "WARNING",
                        "title": f"Recursive Function '{f}' without Memoization",
                        "why_deducted": f"Function '{f}' calls itself recursively without apparent memoization or dynamic programming cache, leading to exponential runtime.",
                        "how_to_reach_100": "Store already computed return values in a table (e.g. vector<int> memo or unordered_map) before recurring.",
                        "code_fix": "// Memoization cache:\nvector<int> memo(N, -1);\nint " + f + "(int n) {\n    if (memo[n] != -1) return memo[n];\n    // ...\n    return memo[n] = result;\n}"
                    })
                    break

    efficiency_checks_flagged = min(len(inefficiencies), efficiency_checks_total)
    efficiency_score = (
        100.0 * (efficiency_checks_total - efficiency_checks_flagged) / efficiency_checks_total
        if efficiency_checks_total
        else 100.0
    )
    inefficient_pct = 100.0 - efficiency_score
    for deduction in deductions:
        if deduction["category"] == "Security":
            deduction["penalty"] = 100.0 / security_checks_total
        elif deduction["category"] == "Efficiency":
            deduction["penalty"] = 100.0 / efficiency_checks_total

    return {
        "security_score": round(security_score, 1),
        "insecure_pct": round(insecure_pct, 1),
        "efficiency_score": round(efficiency_score, 1),
        "inefficient_pct": round(inefficient_pct, 1),
        "security_checks_total": security_checks_total,
        "security_checks_flagged": security_checks_flagged,
        "security_risk_weight": security_risk_weight,
        "efficiency_checks_total": efficiency_checks_total,
        "efficiency_checks_flagged": efficiency_checks_flagged,
        "score_basis": "configured_static_checks",
        "critical_threats": critical_count,
        "advisory_threats": warning_count + risk_count,
        "inefficiency_notes": inefficiencies,
        "deductions": deductions,
        "total_loc": total_loc,
    }