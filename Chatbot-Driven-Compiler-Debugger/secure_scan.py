"""
secure_scan.py - Static Pre-Execution Security Scanner and Guardrail Engine
=============================================================================
Inspects C++ source code for malicious constructs, dangerous OS calls, network
exfiltration, path traversal, shellcode, and memory safety vulnerabilities
WITHOUT executing the code.
"""

import re

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
    r'\b(socket|connect|bind|listen|accept)\s*\(': "CRITICAL: Socket communication function detected. Network connections are prohibited.",
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


def run_security_guardrail(code_snippet):
    """
    Scans code for banned C++ patterns and returns (is_safe, warning_message).
    Blocks if any critical security, OS command, network, filesystem or evasion pattern matches.
    PERFORMANCE patterns are excluded from blocking.
    """
    for pattern, warning in SECURITY_PATTERNS.items():
        if re.search(pattern, code_snippet):
            if warning.startswith("PERFORMANCE:"):
                continue
            return False, warning

    return True, "Code looks safe."


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