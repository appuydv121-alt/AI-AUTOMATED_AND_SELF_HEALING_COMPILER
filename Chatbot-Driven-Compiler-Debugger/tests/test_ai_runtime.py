import json
import unittest
from unittest.mock import Mock, patch

from ai_providers import (
    GroqProvider,
    LLMProvider,
    OllamaProvider,
    OpenRouterProvider,
    resolve_provider_chain,
)
import llm_provider
from live_suggester import (
    apply_suggestion_to_code,
    extract_cpp_code_block,
    generate_optimal_program,
    output_equal,
    parse_suggestions_json,
    render_live_suggestions,
    run_self_refine_loop,
    validate_optimized_program,
)


class TestSuggestionParsing(unittest.TestCase):
    def test_parse_valid_json(self):
        payload = '{"items": [{"line_start": 2, "line_end": 3, "severity": "improvement", "issue": "Use std::vector", "reason": "Faster", "original": "int x = 0;", "optimized": "int x = 0;"}]}'
        self.assertEqual(parse_suggestions_json(payload)[0]["line_start"], 2)

    def test_parse_malformed_json(self):
        self.assertEqual(parse_suggestions_json('```json\nnot json\n```'), [])

    def test_parse_fenced_json(self):
        payload = '```json\n[{"line_start": 1, "line_end": 1, "severity": "info", "issue": "test", "reason": "issue", "original": "x", "optimized": "y"}]\n```'
        self.assertEqual(len(parse_suggestions_json(payload)), 1)

    def test_apply_suggestion_to_code_replaces_lines(self):
        code = "int main() {\n    int x = 0;\n    return x;\n}\n"
        revised = apply_suggestion_to_code(code, 2, 2, "    int x = 1;\n")
        self.assertIn("int x = 1;", revised)
        self.assertNotIn("int x = 0;", revised)

    def test_original_line_match_requires_content_match(self):
        code = "int main() {\n    int x = 0;\n    return x;\n}\n"
        self.assertTrue(apply_suggestion_to_code(code, 2, 2, "    int x = 1;\n") is not None)


class TestProviderFallback(unittest.TestCase):
    def test_provider_chain_prefers_ollama(self):
        chain = resolve_provider_chain("ollama")
        self.assertEqual(chain[0], "ollama")

    def test_429_backoff_and_fallback(self):
        class FakeProvider(LLMProvider):
            def __init__(self, name):
                self.name = name

            def is_available(self):
                return True

            def list_models(self):
                return ["model-a"]

            def generate(self, prompt, system=None, **kwargs):
                raise RuntimeError("429 rate limited")

        providers = [FakeProvider("cloud"), FakeProvider("fallback")]
        with patch("ai_providers._http_post", side_effect=RuntimeError("429 rate limited")):
            self.assertRaises(RuntimeError, lambda: providers[0].generate("hi"))


class TestOllamaTaskRouting(unittest.TestCase):
    @patch("llm_provider._cache_put")
    @patch("llm_provider._call_ollama", return_value='{"ok": true}')
    @patch("llm_provider._cache_get", return_value=None)
    def test_explicit_ollama_provider_calls_ollama(self, _cache_get, call_ollama, _cache_put):
        result, provider, model = llm_provider.generate_task(
            "default",
            "Return JSON.",
            provider_name="ollama",
            ollama_model="qwen2.5-coder:7b",
            max_tokens=2048,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual((provider, model), ("ollama", "qwen2.5-coder:7b"))
        self.assertEqual(call_ollama.call_args.kwargs["max_tokens"], 2048)

    @patch("llm_provider._call_ollama")
    def test_disabled_provider_is_rejected(self, call_ollama):
        with self.assertRaisesRegex(RuntimeError, "Only the Ollama provider is enabled"):
            llm_provider.generate_task(
                "default",
                "Return JSON.",
                provider_name="groq",
            )

        call_ollama.assert_not_called()

    @patch("llm_provider._cache_put")
    @patch("llm_provider._call_ollama")
    @patch("llm_provider._cache_get", return_value={"ok": True})
    def test_cache_hit_does_not_call_provider(self, _cache_get, call_ollama, _cache_put):
        events = []
        result, provider, model = llm_provider.generate_task(
            "default",
            "Return JSON.",
            provider_name="ollama",
            progress_callback=events.append,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider, "ollama")
        self.assertEqual(model, llm_provider._env("LIVE_MODEL", "qwen2.5-coder:3b"))
        self.assertEqual(events, [{"stage": "cached"}])
        call_ollama.assert_not_called()

    @patch("requests.post")
    @patch("llm_provider._ollama_model_available", return_value=(True, ""))
    @patch("llm_provider._env")
    def test_optimal_code_request_uses_full_output_budget(self, mock_env, _available, mock_post):
        mock_env.side_effect = lambda name, default="": {
            "OLLAMA_HOST": "http://localhost:11434",
            "OLLAMA_NUM_CTX": "8192",
        }.get(name, default)
        response = Mock(status_code=200)
        response.json.return_value = {"response": "complete"}
        mock_post.return_value = response

        with patch.dict(llm_provider._OLLAMA_MAX_TOKENS, {"optimal_code": 4096}):
            llm_provider._call_ollama(
                "optimal_code",
                "Return a complete program.",
                model="qwen2.5-coder:7b",
            )

        options = mock_post.call_args.kwargs["json"]["options"]
        self.assertEqual(options["num_predict"], 4096)
        self.assertEqual(options["num_ctx"], 8192)

    @patch("ai_providers.requests.post")
    def test_ollama_stream_reports_real_generation_events(self, mock_post):
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            json.dumps({"response": "READY", "done": False}).encode(),
            json.dumps({"response": "", "done": True, "eval_count": 2}).encode(),
        ]
        mock_post.return_value = response
        provider = OllamaProvider(model="qwen2.5-coder:7b")
        events = []

        answer = provider.generate("test", progress_callback=events.append)

        self.assertEqual(answer, "READY")
        self.assertEqual(events[0]["characters"], 5)
        self.assertEqual(events[-1], {"stage": "done", "characters": 5, "tokens": 2})
        self.assertTrue(mock_post.call_args.kwargs["stream"])


class TestOptimizationValidation(unittest.TestCase):
    def test_cpp_block_extraction(self):
        text = 'Here is the fix:\n```cpp\nint main(){return 0;}\n```\nThanks'
        self.assertIn("int main()", extract_cpp_code_block(text))

    def test_output_equality(self):
        self.assertTrue(output_equal("hello\n", "hello\n"))
        self.assertFalse(output_equal("hello\n", "world\n"))

    def test_reject_unsafe_optimized_code(self):
        code = 'int main(){ system("rm -rf /"); return 0; }'
        self.assertFalse(validate_optimized_program(code, ""))

    @patch("live_suggester.compile_and_run", return_value=(0, "", ""))
    def test_optimized_program_can_retain_performance_header(self, mock_compile):
        code = "#include <bits/stdc++.h>\nint main() { return 0; }"
        self.assertTrue(validate_optimized_program(code))
        mock_compile.assert_called_once_with(code)

    @patch("llm_provider.generate_task")
    def test_render_live_suggestions_handles_timeout(self, mock_gen):
        mock_gen.side_effect = RuntimeError("Read timed out")
        result = render_live_suggestions(code="int main(){ return 0; }", provider_name="ollama", model="llama3", max_items=3)
        suggestions = result[0] if isinstance(result, tuple) else result
        self.assertEqual(suggestions, [])

    @patch("llm_provider.generate_task")
    def test_live_suggestion_recovers_when_model_miscounts_lines(self, mock_gen):
        mock_gen.return_value = (json.dumps({
            "suggestions": [{
                "line_start": 3,
                "line_end": 3,
                "severity": "error",
                "issue": "Invalid conversion",
                "reason": "A string cannot initialize an integer.",
                "original": 'int age = "twenty";',
                "optimized": "int age = 20;",
            }],
            "analysis": {"specification": "", "current_complexity": "", "alternative": "",
                         "alternative_complexity": "", "tradeoffs": [], "recommendation": ""}
        }), "ollama", "qwen2.5-coder:7b")
        code = '#include <iostream>\nusing namespace std;\n\nint main() {\n    int age = "twenty";\n    return 0;\n}'

        result = render_live_suggestions(code=code, provider_name="ollama", model="qwen2.5-coder:7b")
        suggestions = result[0] if isinstance(result, tuple) else result

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["line_start"], 5)
        self.assertEqual(suggestions[0]["severity"], "warning")

    @patch("llm_provider.generate_task")
    def test_live_logic_suggestion_survives_existing_performance_warning(self, mock_gen):
        mock_gen.return_value = (json.dumps({
            "suggestions": [{
                "line_start": 3,
                "line_end": 3,
                "severity": "improvement",
                "issue": "Use an immutable value",
                "reason": "The value is not modified after initialization.",
                "original": "int value = 1;",
                "optimized": "const int value = 1;",
            }],
            "analysis": {"specification": "", "current_complexity": "", "alternative": "",
                         "alternative_complexity": "", "tradeoffs": [], "recommendation": ""}
        }), "ollama", "qwen2.5-coder:7b")
        code = "#include <bits/stdc++.h>\nint main() {\n    int value = 1;\n    return value;\n}"

        result = render_live_suggestions(code=code, provider_name="ollama", model="qwen2.5-coder:7b")
        suggestions = result[0] if isinstance(result, tuple) else result

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["issue"], "Use an immutable value")

    @patch("llm_provider.generate_task")
    def test_live_suggestion_rejects_noop_replacement(self, mock_gen):
        mock_gen.return_value = (json.dumps({
            "suggestions": [{
                "line_start": 2,
                "line_end": 2,
                "severity": "improvement",
                "issue": "Move initialization",
                "reason": "This is faster.",
                "original": "int value = 1;",
                "optimized": "int value = 1;",
            }],
            "analysis": {"specification": "", "current_complexity": "", "alternative": "",
                         "alternative_complexity": "", "tradeoffs": [], "recommendation": ""}
        }), "ollama", "qwen2.5-coder:7b")

        result = render_live_suggestions(
            code="int main() {\n    int value = 1;\n    return value;\n}",
            provider_name="ollama",
            model="qwen2.5-coder:7b",
        )
        suggestions = result[0] if isinstance(result, tuple) else result
        self.assertEqual(suggestions, [])

    @patch("llm_provider.generate_task")
    def test_full_function_suggestion_expands_source_range(self, mock_gen):
        mock_gen.return_value = (json.dumps({
            "suggestions": [{
                "line_start": 4,
                "line_end": 4,
                "severity": "improvement",
                "issue": "Use a single pass for the calculation.",
                "reason": "This avoids repeated scans.",
                "original": "int value = input[index];",
                "optimized": "int calculate(const vector<int>& input) { return 0; }",
            }],
            "analysis": {"specification": "", "current_complexity": "", "alternative": "",
                         "alternative_complexity": "", "tradeoffs": [], "recommendation": ""}
        }), "ollama", "qwen2.5-coder:7b")
        code = "int calculate(const vector<int>& input) {\n    int index = 0;\n    int value = input[index];\n    return value;\n}"

        result = render_live_suggestions(code=code, provider_name="ollama", model="qwen2.5-coder:7b")
        suggestions = result[0] if isinstance(result, tuple) else result

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["line_start"], 1)
        self.assertEqual(suggestions[0]["line_end"], 5)
        self.assertEqual(suggestions[0]["original"], code)

    @patch("llm_provider.generate_task")
    def test_live_suggestions_emit_structured_analysis(self, mock_gen):
        mock_gen.return_value = (json.dumps({
            "analysis": {
                "specification": "Find the longest sequence.",
                "current_complexity": {"time": "O(n^2)", "space": "O(n)"},
                "alternative": "Start only at sequence boundaries.",
                "alternative_complexity": {"time": "O(n)", "space": "O(n)"},
                "tradeoffs": [],
                "recommendation": "change",
            },
            "suggestions": [],
        }), "ollama", "qwen2.5-coder:7b")
        events = []

        render_live_suggestions(
            code="int main() { return 0; }",
            provider_name="ollama",
            model="qwen2.5-coder:7b",
            progress_callback=events.append,
        )

        analysis_event = next(event for event in events if event["stage"] == "analysis")
        self.assertEqual(analysis_event["analysis"]["current_complexity"]["time"], "O(n^2)")

    @patch("live_suggester.run_self_refine_loop")
    @patch("live_suggester.run_security_guardrail")
    @patch("live_suggester.compile_and_run")
    @patch("llm_provider.generate_task")
    def test_generate_optimal_program_returns_single_model_response_without_verification(
        self, mock_gen, mock_compile, mock_security, mock_refine
    ):
        generated = "int main(){ return 42; }"
        mock_gen.return_value = (f"```cpp\n{generated}\n```", "ollama", "llama3.1:8b")

        result = generate_optimal_program(
            "int main(){ return 0; }",
            [],
            {"return_code": 0},
            provider_name="ollama",
            ollama_model="llama3.1:8b",
        )

        self.assertEqual(result[0], generated)
        self.assertEqual(result[2], "Generated (not verified)")
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.args[0], "optimal_code")
        self.assertEqual(mock_gen.call_args.kwargs["ollama_model"], "llama3.1:8b")
        mock_compile.assert_not_called()
        mock_security.assert_not_called()
        mock_refine.assert_not_called()

    @patch("llm_provider.generate_task", return_value=("print('ready')", "ollama", "model"))
    def test_generate_optimal_program_accepts_unfenced_source(self, _mock_gen):
        result = generate_optimal_program("print('old')", language="python")

        self.assertEqual(result[0], "print('ready')")
        self.assertEqual(result[2], "Generated (not verified)")

    def test_self_refine_loop_limit(self):
        attempts = []

        def fake_generate(prompt, system=None, **kwargs):
            attempts.append(1)
            return "```cpp\nint main(){ system(\"echo hi\"); return 0; }\n```"

        result = run_self_refine_loop(fake_generate, "int main(){ return 0; }", max_attempts=3)
        self.assertEqual(result[0], False)
        self.assertLessEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
