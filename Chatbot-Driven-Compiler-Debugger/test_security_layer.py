"""Unit tests for security_layer.py and compiler_service.py perimeter integration."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from security_layer import (
    IsolationForestDetector,
    OwaspWafFilter,
    RateLimiterFail2ban,
    evaluate_request_security,
)
from compiler_service import compile_and_run


class TestSecurityLayer(unittest.TestCase):
    def test_normal_cpp_passes_perimeter(self):
        code = (
            "#include <iostream>\n"
            "int main() {\n"
            "    int a = 5, b = 10;\n"
            "    std::cout << a + b << std::endl;\n"
            "    return 0;\n"
            "}\n"
        )
        res = evaluate_request_security(
            code,
            stdin_input="10 20\n",
            client_id="test-normal",
            waf=OwaspWafFilter(),
            iforest=IsolationForestDetector(),
            rate_limiter=RateLimiterFail2ban(),
        )
        self.assertTrue(res.allowed, f"Normal C++ should pass, got: {res.reason}")
        self.assertLess(res.anomaly_score, 0.58)

    def test_owasp_waf_blocks_xss_sqli_cmdi_traversal(self):
        waf = OwaspWafFilter()
        ok1, r1, _ = waf.inspect(code="int main(){}", stdin_input="<script>alert(1)</script>")
        self.assertFalse(ok1)
        self.assertEqual(r1, "CRS-941100")

        ok2, r2, _ = waf.inspect(code="int main(){}", stdin_input="' OR 1=1 --")
        self.assertFalse(ok2)
        self.assertEqual(r2, "CRS-942100")

        ok3, r3, _ = waf.inspect(code="int main(){}", stdin_input="foo; rm -rf /")
        self.assertFalse(ok3)
        self.assertEqual(r3, "CRS-932100")

        ok4, r4, _ = waf.inspect(code='const char* p = "../../etc/passwd";', stdin_input="")
        self.assertFalse(ok4)
        self.assertEqual(r4, "CRS-930100")

    def test_isolation_forest_flags_obfuscated_payload(self):
        detector = IsolationForestDetector()
        normal_code = "#include <iostream>\nint main(){ std::cout << 42; return 0; }\n"
        ok_norm, score_norm, _ = detector.inspect(normal_code)
        self.assertTrue(ok_norm, f"Normal C++ should pass, score={score_norm}")
        self.assertLess(score_norm, 0.58)

        # High suspicious token density + extreme one-line length + burst rate
        obfuscated = (
            "\\x90\\x41\\x42\\x43" * 25
            + " base64 /bin/sh wget curl chmod +x " * 10
        )
        ok_anom, score_anom, reason = detector.inspect(obfuscated, stdin_input="A" * 5000, recent_rate=25.0)
        self.assertFalse(ok_anom, f"Obfuscated payload should be flagged, score={score_anom}")
        self.assertGreaterEqual(score_anom, 0.58)
        self.assertIn("IsolationForest", reason)

    def test_rate_limiter_and_fail2ban_jail(self):
        limiter = RateLimiterFail2ban(max_requests=2, window_seconds=60.0, ban_threshold=2, ban_duration=60.0)
        ok1, rem1, _, _ = limiter.check_and_consume("10.0.0.5")
        self.assertTrue(ok1)
        self.assertEqual(rem1, 1)

        ok2, rem2, _, _ = limiter.check_and_consume("10.0.0.5")
        self.assertTrue(ok2)
        self.assertEqual(rem2, 0)

        # Strike 1 (rate limit exceeded)
        ok3, _, ban3, _ = limiter.check_and_consume("10.0.0.5")
        self.assertFalse(ok3)
        self.assertIsNone(ban3)

        # Strike 2 -> triggers Fail2ban jail
        ok4, _, ban4, reason4 = limiter.check_and_consume("10.0.0.5")
        self.assertFalse(ok4)
        self.assertIsNotNone(ban4)
        self.assertIn("Fail2ban", reason4)

    def test_compile_and_run_blocks_waf_input(self):
        res = compile_and_run(
            "#include <iostream>\nint main(){ return 0; }\n",
            stdin_input="<script>alert(1)</script>",
            client_id="test-compile-waf",
        )
        self.assertEqual(res.status_code, 1)
        self.assertIn("PERIMETER SECURITY BLOCKED", res.stderr)


if __name__ == "__main__":
    unittest.main()
