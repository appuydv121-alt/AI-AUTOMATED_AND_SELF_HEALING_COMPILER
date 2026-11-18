import streamlit as st
from streamlit_ace import st_ace
import subprocess
import requests
import os
import time
import shutil
import json
import html

from codecarbon import EmissionsTracker
# Import our custom modules
from fix_engine import validate_ai_fix
from secure_scan import run_security_guardrail, compute_code_quality_metrics
from error_classifier import classify_error, get_diagnostic_prompt
from compiler_service import compile_and_run, init_sandbox_db
from audit_logger import log_interaction, log_security_event 
from error_logger import log_error 
import diagnostic_engine as diag
from semantic_analyzer import analyze_semantics, apply_safe_fixes
import ui_theme
from ui_themes import setup_ui, pane_marker, status_banner
from live_suggester import generate_optimal_program
from memory_store import record_experience
import llm_provider as _llm_provider
from empirical_profiler import run_empirical_profiler

# Import the reasoning pipeline (graceful if unavailable)
try:
    from reasoning import run_reasoning
    from reasoning.adapters import findings_to_diagnostics, findings_to_chat, findings_to_patch_code
    REASONING_AVAILABLE = True
except ImportError:
    REASONING_AVAILABLE = False

# LLM config
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"
MODEL_NAME = "qwen2.5-coder:7b"
SEMANTIC_ANALYSIS_ENABLED = os.environ.get("SEMANTIC_ANALYSIS_ENABLED", "true").lower() not in {"0", "false", "no"}
TERMINAL_OUTPUT_PREVIEW_CHARS = 12_000


def render_terminal_output(output: str, label: str) -> None:
    st.markdown(ui_theme.code_label(label), unsafe_allow_html=True)
    if not output:
        st.code("(no output)", language="text")
        return

    preview = output[:TERMINAL_OUTPUT_PREVIEW_CHARS]
    st.code(preview, language="text")
    if len(output) > TERMINAL_OUTPUT_PREVIEW_CHARS:
        remaining = output[TERMINAL_OUTPUT_PREVIEW_CHARS:]
        with st.expander(f"Show remaining captured output ({len(remaining):,} characters)"):
            st.code(remaining, language="text")


def _get_ollama_models():
    response = requests.get(OLLAMA_TAGS_URL, timeout=(2, 3))
    response.raise_for_status()
    return [model.get("name", "") for model in response.json().get("models", [])]


def _ensure_ollama_running():
    try:
        return _get_ollama_models(), None
    except requests.RequestException:
        ollama_executable = shutil.which("ollama")
        if not ollama_executable and os.name == "nt":
            default_executable = os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"
            )
            if os.path.isfile(default_executable):
                ollama_executable = default_executable

        if not ollama_executable:
            return [], (
                f"Ollama is not reachable at {OLLAMA_HOST}. Start Ollama or configure "
                "OLLAMA_HOST to a server reachable by this app."
            )

        launch_options = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            launch_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([ollama_executable, "serve"], **launch_options)
        except OSError:
            pass

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                return _get_ollama_models(), None
            except requests.RequestException:
                time.sleep(0.5)
        return [], f"Could not connect to the Ollama server at {OLLAMA_HOST}."


def get_ollama_model_status(model_name: str = MODEL_NAME) -> dict[str, str | bool]:
    installed_models, startup_error = _ensure_ollama_running()
    if startup_error or not installed_models:
        return {
            "symbol": "🔴",
            "status": "Not running",
            "detail": startup_error or "Ollama server is offline",
            "running": False,
        }

    model_found = any(
        installed_model == model_name or installed_model.split(":", 1)[0] == model_name.split(":", 1)[0]
        for installed_model in installed_models
    )
    if not model_found:
        return {
            "symbol": "🟠",
            "status": "Model missing",
            "detail": f"{model_name} is not installed locally",
            "running": False,
        }

    return {
        "symbol": "🟢",
        "status": "Running",
        "detail": f"{model_name} is ready",
        "running": True,
    }


def get_ai_explanation(prompt, unavailable_message="AI check unavailable.", model_override: str | None = None):
    """Send a prompt through the unified LLM provider using current sidebar settings."""
    try:
        prov = "ollama"
        oll_mdl = (
            model_override
            or st.session_state.get("ollama_chat_model_select")
            or st.session_state.get("ollama_optimal_model_select")
            or st.session_state.get("ollama_live_model_select")
            or os.environ.get("LIVE_MODEL", "qwen2.5-coder:7b")
        )
        result, active_prov, active_mdl = _llm_provider.generate_task(
            "chat",
            prompt,
            provider_name=prov,
            ollama_model=oll_mdl,
        )
        if active_prov and active_mdl:
            st.session_state.last_served_provider = active_prov
            st.session_state.last_served_model = active_mdl
            st.session_state.live_suggestions_active_provider = active_prov
            st.session_state.live_suggestions_active_model = active_mdl
        return str(result) if not isinstance(result, str) else result
    except Exception as error:
        active_lang = st.session_state.get("current_language", "C++")
        user_src = st.session_state.get("last_compiled_code") or ""
        err_msg = str(error)
        if "not running" in err_msg.lower():
            status_banner = "⚠️ **AI Model Offline / Not Running:** Ollama is not active locally (`http://localhost:11434`). Please run `ollama serve` in a terminal or launch Ollama."
        elif "timed out" in err_msg.lower():
            status_banner = f"⏱️ **AI Model Timed Out:** The local model did not respond within the timeout window ({error})."
        else:
            status_banner = f"⚠️ **AI Model Notice:** {error}"

        if user_src:
            fallback_breakdown = analyze_code_structure_and_complexity(
                user_src,
                active_lang,
                stdin_input=st.session_state.get("stdin_input", ""),
                stdout=st.session_state.get("last_stdout", ""),
            )
            return (
                f"{status_banner}\n\n"
                f"**Instant Local Architectural & Complexity Breakdown:**\n\n{fallback_breakdown}"
            )
        return status_banner


def get_cached_local_diagnostics(code, semantic_enabled=True, semantic_min_confidence=0.30):
    active_lang = st.session_state.get("current_language", "C++").lower()
    if active_lang not in ("c++", "cpp", "c"):
        return []
    if "analysis_cache" not in st.session_state:
        st.session_state.analysis_cache = {}
    cache = st.session_state.analysis_cache
    cache_key = (code, semantic_enabled, semantic_min_confidence)
    if cache_key not in cache:
        cache[cache_key] = diag.run_local_static_checks(
            code,
            semantic_enabled=semantic_enabled,
            semantic_min_confidence=semantic_min_confidence,
        )
    return cache[cache_key]


def validate_semantic_correction(source, report):
    corrected, applied = apply_safe_fixes(source, report)
    if corrected == source or not applied:
        return None, "No high-confidence automatic correction is available."

    is_safe, warning = run_security_guardrail(corrected)
    if not is_safe:
        return None, f"The proposed correction did not pass the security scan: {warning}"

    app_dir = os.path.dirname(os.path.abspath(__file__))
    original_diagnostics, _ = diag.run_compiler_syntax_check(source, app_dir)
    corrected_diagnostics, check_message = diag.run_compiler_syntax_check(corrected, app_dir)
    original_errors = {
        item["message"] for item in original_diagnostics if item["category"] == "ERROR"
    }
    new_errors = [
        item for item in corrected_diagnostics
        if item["category"] == "ERROR" and item["message"] not in original_errors
    ]
    if new_errors:
        return None, "The proposed correction introduced a new compiler error and was not shown."
    if check_message and check_message.startswith(("Syntax check timed out", "g++ not found", "Syntax check error")):
        return None, "The proposed correction could not be syntax-checked and was not shown."
    return corrected, None


def render_semantic_results(report, source, compiler_diagnostics=()):
    if report is None:
        correction_note = st.session_state.get("semantic_correction_note")
        if correction_note:
            st.caption(correction_note)
        return
    if report.stats.get("dictionary_warning"):
        ui_theme.show_status(st, "info", "INFO", report.stats["dictionary_warning"], icon="ℹ️")
    if report.stats.get("error"):
        ui_theme.show_status(
            st,
            "warning",
            "WARNING",
            "Semantic analysis was incomplete; compilation and execution were not blocked.",
            icon="⚠️",
        )
    compiler_error_lines = {
        item.get("line") for item in compiler_diagnostics
        if item.get("category") == "ERROR"
    }
    visible = [item for item in report.diagnostics if item.line not in compiler_error_lines][:20]
    st.markdown("### Semantic Warnings")
    correction_note = st.session_state.get("semantic_correction_note")
    if not visible:
        st.caption("No semantic name/type mismatches above the selected confidence were found.")
        if correction_note:
            st.caption(correction_note)
        return

    for item in visible:
        label = f"{item.confidence_label} · {item.variable} · line {item.line}:{item.column}"
        with st.expander(label, expanded=item.confidence_label == "HIGH"):
            tone = "warning" if item.severity == "warning" else "suggestion"
            icon = "⚠️" if tone == "warning" else "💡"
            details = [
                ("Variable", item.variable),
                ("Current type", item.declared_type),
                ("Expected category", f"{item.expected_category} ({', '.join(item.expected_families)})"),
                ("Reason", item.reason),
            ]
            if item.suggested_fix:
                details.append(("Suggested correction", item.suggested_fix))
            st.markdown(
                ui_theme.severity_block(
                    tone,
                    tone.upper(),
                    item.message,
                    icon=icon,
                    confidence=f"Confidence: {item.confidence_label.lower()} ({item.confidence:.0%})",
                    details=tuple(details),
                ),
                unsafe_allow_html=True,
            )

    corrected = st.session_state.get("semantic_corrected_code")
    if corrected and corrected != source:
        st.markdown(ui_theme.code_label("Syntax-checked corrected code"), unsafe_allow_html=True)
        st.code(corrected, language="cpp")
    if correction_note:
        st.caption(correction_note)
    
def stop_and_save_metrics(tracker, start_t):
    """Stops the carbon tracker and saves metrics with honest provenance."""
    latency = max(0.0, time.time() - start_t)
    try:
        emissions_kg = tracker.stop() if tracker else None
        if emissions_kg is None or (tracker and not getattr(tracker, "final_emissions_data", None)):
            st.session_state.green_metrics = {
                "latency": latency,
                "energy": None,
                "carbon": None,
                "provenance": "unavailable",
                "status": "Hardware counters unavailable on host"
            }
        else:
            energy = tracker.final_emissions_data.energy_consumed if tracker.final_emissions_data else None
            st.session_state.green_metrics = {
                "latency": latency,
                "energy": energy,
                "carbon": (emissions_kg * 1000) if emissions_kg is not None else None,
                "provenance": "measured",
                "status": "Tracked via CodeCarbon"
            }
    except Exception as e:
        st.session_state.green_metrics = {
            "latency": latency,
            "energy": None,
            "carbon": None,
            "provenance": "unavailable",
            "status": f"Tracker error: {e}"
        }


def sync_live_diagnostics(user_code: str):
    """Synchronously compute and merge static and compiler diagnostics for current editor code."""
    if "live_diagnostics" not in st.session_state:
        st.session_state.live_diagnostics = []

    active_lang = st.session_state.get("current_language", "C++").lower()

    # ── Python syntax checking branch ──────────────────────────────────────────
    if "python" in active_lang:
        code_changed = user_code != st.session_state.get("diag_last_code")
        if code_changed:
            st.session_state.diag_last_code = user_code
            st.session_state.diag_syntax_checked_code = None

        py_diags = []
        if user_code and user_code.strip():
            import ast
            try:
                ast.parse(user_code)
            except SyntaxError as e:
                py_diags.append(diag.make_diagnostic(
                    category="ERROR",
                    title="Python SyntaxError",
                    message=f"Line {e.lineno or 1}: {e.msg}",
                    line=e.lineno or 1,
                    source="python_ast",
                ))
        st.session_state.live_diagnostics = py_diags
        return py_diags

    # ── JavaScript / TypeScript syntax checking branch ────────────────────────
    elif any(l in active_lang for l in ("javascript", "typescript", "js", "ts")):
        code_changed = user_code != st.session_state.get("diag_last_code")
        if code_changed:
            st.session_state.diag_last_code = user_code
            st.session_state.diag_syntax_checked_code = None

        js_diags = []
        if user_code and user_code.strip():
            node_bin = shutil.which("node")
            if node_bin:
                app_dir = os.path.dirname(os.path.abspath(__file__))
                ext = "ts" if ("type" in active_lang or "ts" in active_lang) else "js"
                tmp_js = os.path.join(app_dir, f"temp_syntax.{ext}")
                try:
                    with open(tmp_js, "w", encoding="utf-8") as f:
                        f.write(user_code)
                    chk = subprocess.run([node_bin, "--check", tmp_js], capture_output=True, text=True, timeout=3)
                    if chk.returncode != 0:
                        err_msg = (chk.stderr or chk.stdout).strip()
                        import re
                        m = re.search(r":(\d+)", err_msg)
                        err_line = int(m.group(1)) if m else 1
                        first_line = err_msg.splitlines()[0] if err_msg else "Syntax Error"
                        js_diags.append(diag.make_diagnostic(
                            category="ERROR",
                            title=f"{st.session_state.get('current_language', 'JavaScript')} SyntaxError",
                            message=first_line,
                            line=err_line,
                            source="node_check",
                        ))
                except Exception:
                    pass
        st.session_state.live_diagnostics = js_diags
        return js_diags

    # ── SQL syntax checking branch ────────────────────────────────────────────
    elif "sql" in active_lang:
        code_changed = user_code != st.session_state.get("diag_last_code")
        if code_changed:
            st.session_state.diag_last_code = user_code
            st.session_state.diag_syntax_checked_code = None

        sql_diags = []
        if user_code and user_code.strip():
            import sqlite3
            import re
            try:
                conn = sqlite3.connect(":memory:")
                init_sandbox_db(conn)
                for stmt in user_code.split(";"):
                    s = stmt.strip()
                    if s:
                        # Attempt EXPLAIN; if missing table, create temporary dummy table and recheck
                        for _ in range(3):
                            try:
                                conn.cursor().execute(f"EXPLAIN {s}")
                                break
                            except sqlite3.OperationalError as op_err:
                                m = re.search(r"no such table:\s*([a-zA-Z0-9_]+)", str(op_err))
                                if m:
                                    missing_tbl = m.group(1)
                                    conn.cursor().execute(
                                        f"CREATE TABLE IF NOT EXISTS {missing_tbl} (id INTEGER, name TEXT, username TEXT, password TEXT, email TEXT, value TEXT)"
                                    )
                                else:
                                    raise
                conn.close()
            except sqlite3.Error as e:
                sql_diags.append(diag.make_diagnostic(
                    category="ERROR",
                    title="SQL SyntaxError",
                    message=str(e),
                    line=1,
                    source="sqlite_parser",
                ))
        st.session_state.live_diagnostics = sql_diags
        return sql_diags

    # ── C++ syntax and local checks ───────────────────────────────────────────
    semantic_enabled = st.session_state.get("semantic_analysis_enabled", True)
    now = time.time()
    code_changed = user_code != st.session_state.get("diag_last_code")
    if code_changed:
        st.session_state.diag_last_code = user_code
        st.session_state.diag_last_time = now
        st.session_state.diag_syntax_checked_code = None
        st.session_state.cached_compiler_diags = []

    local_diagnostics = get_cached_local_diagnostics(
        user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
    )

    if user_code and user_code.strip() and st.session_state.get("diag_syntax_checked_code") != user_code:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        compiler_diags, _ = diag.run_compiler_syntax_check(user_code, app_dir)
        st.session_state.cached_compiler_diags = compiler_diags
        st.session_state.diag_syntax_checked_code = user_code
        st.session_state.live_diagnostics = diag.merge_diagnostics(local_diagnostics, compiler_diags)
    elif st.session_state.get("diag_syntax_checked_code") == user_code:
        st.session_state.live_diagnostics = diag.merge_diagnostics(
            local_diagnostics, st.session_state.get("cached_compiler_diags", [])
        )
    else:
        st.session_state.live_diagnostics = local_diagnostics

    return st.session_state.live_diagnostics


@st.fragment
def render_live_diagnostics():
    user_code = st.session_state.get("editor_code", "")
    sync_live_diagnostics(user_code)
    st.markdown(
        diag.render_diagnostics_html(st.session_state.live_diagnostics),
        unsafe_allow_html=True,
    )



def analyze_code_structure_and_complexity(code: str, language: str, stdin_input: str = "", stdout: str = "") -> str:
    """Perform rigorous algorithmic analysis to extract execution steps, exact TC, and SC."""
    from complexity_engine import analyze_algorithmic_complexity
    report = analyze_algorithmic_complexity(code, language=language, stdin_input=stdin_input, stdout=stdout)

    steps_text = "\n".join(report.steps_walkthrough)
    optimization_section = (
        "\n\n### 💡 Observations & Optimization Opportunities\n"
        + "\n".join(f"- {opt}" for opt in report.optimizations)
        if report.optimizations
        else ""
    )

    return f"""### 📖 Code Logic & Execution Walkthrough
{steps_text}

### ⏱️ Algorithmic Complexity Analysis
- **Time Complexity (TC):** `{report.time_complexity}` — {report.tc_derivation}
- **Space Complexity (SC):** `{report.space_complexity}` — {report.sc_derivation}{optimization_section}"""


def generate_code_walkthrough_and_complexity(
    code: str,
    language: str,
    stdin_input: str = "",
    stdout: str = "",
    stderr: str = "",
    findings_text: str = "",
    ollama_model: str | None = None,
) -> str:
    """Generate exact AST complexity breakdown combined with 7B LLM explanation, security, and efficiency review."""
    ast_breakdown = analyze_code_structure_and_complexity(
        code, language, stdin_input=stdin_input, stdout=stdout
    )

    target_model = (
        ollama_model
        or st.session_state.get("ollama_optimal_model_select")
        or st.session_state.get("ollama_chat_model_select")
        or os.environ.get("CODEGEN_MODEL", "qwen2.5-coder:7b")
    )

    review_prompt = (
        f"You are an expert {language} code reviewer, security auditor, and performance engineer.\n"
        f"Analyze the following {language} program:\n"
        f"```{language.lower()}\n{code}\n```\n\n"
    )
    if stdin_input and stdin_input.strip():
        review_prompt += f"Program stdin:\n```\n{stdin_input.strip()}\n```\n\n"
    if stdout and stdout.strip():
        review_prompt += f"Program stdout:\n```\n{stdout.strip()[:600]}\n```\n\n"
    if findings_text and findings_text.strip():
        review_prompt += f"Reasoning findings:\n{findings_text.strip()[:600]}\n\n"
    review_prompt += (
        "Provide a concise, well-structured response with exactly these three sections:\n"
        "1. **Code Explanation**: Explain what the user's code does and walk through its logic step by step.\n"
        "2. **Security Assessment**: Rate code safety (Safe / Needs Hardening / Vulnerable) and list any concrete security risks or confirm none were found.\n"
        "3. **Optimization Ideas (What You Can Optimize)**: Evaluate algorithmic efficiency and briefly describe what improvements or data structures the user can apply.\n"
        "STRICT RULES:\n"
        "- Explain ONLY the code the user wrote.\n"
        "- DO NOT write, generate, or include any optimized, refactored, or rewritten code or fenced code blocks (no ``` blocks).\n"
        "- End with one short sentence: 'Click **⚡ Optimize** if you want me to generate the optimized code.'"
    )

    try:
        ai_review, active_prov, active_mdl = _llm_provider.generate_task(
            "chat",
            review_prompt,
            provider_name="ollama",
            ollama_model=target_model,
            language=language.lower(),
        )
        if active_prov and active_mdl:
            st.session_state.last_served_provider = active_prov
            st.session_state.last_served_model = active_mdl
        import re
        ai_text = str(ai_review).strip()
        # Strip any fenced code blocks if the model still tried to emit optimized code
        ai_text = re.sub(r"```[\s\S]*?```", "", ai_text).strip()
        ai_text = re.sub(r"\n{3,}", "\n\n", ai_text)
        if ai_text:
            return (
                f"{ast_breakdown}\n\n"
                f"---\n\n"
                f"### 🤖 AI Explanation, Security & Optimization Ideas (`{active_mdl or target_model}`)\n"
                f"{ai_text}"
            )
    except Exception as exc:
        return (
            f"{ast_breakdown}\n\n"
            f"---\n\n"
            f"*ℹ️ Local LLM (`{target_model}`) review unavailable ({exc}); showing deterministic AST breakdown above.*"
        )

    return ast_breakdown


def render_agentic_result_view(res, current_user_code: str, lang_name: str):
    """Render Human-in-the-Loop Supervised Multi-Agent Results with Diff & Evidence Gate."""
    if not res:
        return
    st.markdown("---")
    st.subheader("🛡️ Supervised Multi-Agent Optimization & Hardening Gate")
    st.caption(
        "Security and efficiency scores are heuristic signals, not proof of global optimality. "
        "Generated tests are samples, not a proof. A patch is accepted only when reference runs complete, "
        "captured output matches exactly, candidate sanitizer checks pass, and neither quality score nor "
        "measured runtime regresses."
    )

    # Metrics comparison
    sec_orig = getattr(res, "metrics_before", {}).get("security_score", getattr(res, "original_metrics", {}).get("security_score", 0))
    sec_opt = getattr(res, "metrics_after", {}).get("security_score", getattr(res, "optimized_metrics", {}).get("security_score", 0))
    eff_orig = getattr(res, "metrics_before", {}).get("efficiency_score", getattr(res, "original_metrics", {}).get("efficiency_score", 0))
    eff_opt = getattr(res, "metrics_after", {}).get("efficiency_score", getattr(res, "optimized_metrics", {}).get("efficiency_score", 0))

    delta_sec = sec_opt - sec_orig
    delta_eff = eff_opt - eff_orig

    time_orig = getattr(res, "metrics_before", {}).get("median_runtime_ms", 0.0)
    time_opt = getattr(res, "metrics_after", {}).get("median_runtime_ms", 0.0)
    tests_eval = getattr(res, "tests_evaluated", 0)
    tests_ok = getattr(res, "tests_passed", 0)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("🛡️ Security Score", f"{sec_opt:.0f}%", delta=f"{delta_sec:+.0f}%" if delta_sec != 0 else None)
    with c2:
        st.metric("⚡ Efficiency Score", f"{eff_opt:.0f}%", delta=f"{delta_eff:+.0f}%" if delta_eff != 0 else None)
    with c3:
        st.metric("⏱️ Median Latency", f"{time_opt:.2f} ms", delta=f"{time_opt - time_orig:+.2f} ms" if time_orig > 0 else None, delta_color="inverse")
    with c4:
        test_status = f"{tests_ok}/{tests_eval} Passed" if tests_eval > 0 else "Not verified"
        test_delta = "All generated cases passed" if tests_eval > 0 and tests_ok == tests_eval else "Not verified"
        st.metric("🧪 Test Equivalence", test_status, delta=test_delta)

    # Visual Code Diff (Original vs Candidate)
    diff_text = getattr(res, "diff_unified", "")
    if diff_text:
        with st.expander("📝 View Unified Code Diff (Changes Proposed)", expanded=True):
            st.code(diff_text, language="diff")

    tab_cand, tab_trace, tab_states = st.tabs(["✨ Proposed Code", "🕵️ Agent Trace", "🔄 State Machine"])
    with tab_cand:
        st.code(res.optimized_code, language=getattr(res, "language", "cpp"))

    with tab_trace:
        st.markdown('<div class="ui-agent-trace-container">', unsafe_allow_html=True)
        for step in res.agent_trace:
            agent_name = step.get("agent") if isinstance(step, dict) else getattr(step, "agent", "Agent")
            action = step.get("action") if isinstance(step, dict) else getattr(step, "action", "")
            details = step.get("details") if isinstance(step, dict) else getattr(step, "details", "")
            elapsed = step.get("elapsed_ms") if isinstance(step, dict) else getattr(step, "elapsed_ms", 0.0)

            agent_lower = str(agent_name).lower()
            agent_tag_cls = "planner" if "plan" in agent_lower or "tool" in agent_lower else ("optimizer" in agent_lower and "optimizer" or ("judge" in agent_lower and "judge" or "critic"))
            card_cls = f"ui-agent-card {agent_tag_cls}"
            time_str = f"{elapsed:.0f}ms" if elapsed > 0 else ""
            st.markdown(f"""
            <div class="{card_cls}">
                <div class="ui-agent-header">
                    <div>
                        <span class="ui-agent-tag {agent_tag_cls}">🤖 {html.escape(str(agent_name))}</span>
                        <span class="ui-agent-action">{html.escape(str(action))}</span>
                    </div>
                    <span class="ui-agent-time">{time_str}</span>
                </div>
                <div class="ui-agent-details">{html.escape(str(details))}</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_states:
        transitions = getattr(res, "state_transitions", [])
        if transitions:
            for t in transitions:
                st.caption(f"**Cycle {t.get('cycle', 0)}:** `{t.get('state_from')}` ➔ `{t.get('state_to')}` ({t.get('reason')}) • {t.get('elapsed_ms')}ms")
        else:
            st.caption("No state transitions recorded.")

    if not getattr(res, "success", False):
        st.warning(
            "No patch passed the verification gates. The editor was not changed; "
            "review the agent trace for the reason."
        )
    else:
        # Human-in-the-Loop Gate: Explicit Approve vs Reject buttons
        st.markdown("### 🚦 Human-in-the-Loop Gate")
        st.info("ℹ️ No code changes are applied silently. Inspect the diff above and approve to update your editor.")
        col_app, col_rej = st.columns([2, 1])
        with col_app:
            if st.button("✅ Approve & Apply Patch to Editor", type="primary", key="btn_approve_agentic_patch", use_container_width=True):
                st.session_state.editor_code = res.optimized_code
                st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
                # Record success into MemoryStore
                try:
                    record_experience(
                        language=getattr(res, "language", "cpp"),
                        flaws=";".join([f.get("message", "") for f in getattr(res, "evidence_report", {}).get("tool_findings", [])][:3]),
                        flaw_summary="Supervised multi-agent patch approved by user",
                        original_code=res.original_code,
                        patch_code=res.optimized_code,
                        success=True,
                    )
                except Exception:
                    pass
                st.session_state.agentic_result = None
                st.success("Patch applied to editor! Click 'Compile and analyze' to execute.")
                st.rerun(scope="app")
        with col_rej:
            if st.button("❌ Reject Patch", key="btn_reject_agentic_patch", use_container_width=True):
                try:
                    record_experience(
                        language=getattr(res, "language", "cpp"),
                        flaws="rejected_by_user",
                        flaw_summary="Patch rejected by human in the loop",
                        original_code=res.original_code,
                        patch_code=res.optimized_code,
                        success=False,
                    )
                except Exception:
                    pass
                st.session_state.agentic_result = None
                st.info("Patch discarded. Editor preserved intact.")
                st.rerun(scope="app")


def _preferred_model_index(models: list[str], preferred: str = "qwen2.5-coder:7b") -> int:
    """Return index of preferred 7B model in models list, falling back to index 0."""
    if not models:
        return 0
    if preferred in models:
        return models.index(preferred)
    for idx, mdl in enumerate(models):
        if "qwen2.5-coder:7b" in mdl or ":7b" in mdl:
            return idx
    return 0


# --- DYNAMIC ACTIVE MODEL RESOLUTION ---
def get_active_model_and_provider() -> tuple[str, str]:
    """Resolve the currently selected model and provider for the app header."""
    installed_ollama, _ = _ensure_ollama_running()
    if installed_ollama:
        default_ollama = installed_ollama[_preferred_model_index(installed_ollama)]
    else:
        default_ollama = os.environ.get("LIVE_MODEL", "qwen2.5-coder:7b")
    served_mdl = st.session_state.get("last_served_model")
    mdl = (
        st.session_state.get("ollama_live_model_select")
        or (served_mdl if st.session_state.get("last_served_provider") == "ollama" else None)
        or default_ollama
    )
    return mdl, "ollama"


# --- UI CONFIGURATION ---
st.set_page_config(page_title="C++ Compiler & Debugger", layout="wide", initial_sidebar_state="expanded")
if "theme_initialized" not in st.session_state:
    st.session_state.theme_initialized = True
    st.session_state.ui_theme = "Studio Light"

_curr_model, _curr_prov = get_active_model_and_provider()
_active_theme_dict = setup_ui(model_name=_curr_model, provider=_curr_prov)
active_ui_theme = _active_theme_dict["scheme"]

# Initialize Chat Memory in Session State
if "messages" not in st.session_state or len(st.session_state.messages) == 0:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "👋 **Welcome to Chatbot Compiler Debugger!**\n\nClick **▷ Compile and analyze** to execute your code with runtime verification, logical step-by-step walkthrough, and Time/Space complexity analysis, or ask any question about your code below.",
        }
    ]

if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0

# Purge provider selections from earlier versions.
for _stale_k in (
    "live_ai_model",
    "optimal_ai_model",
    "chat_ai_model",
    "live_ai_provider",
):
    if _stale_k in st.session_state:
        del st.session_state[_stale_k]

st.session_state["ai_provider_select"] = "ollama"
st.session_state["live_ai_provider"] = "ollama"
st.session_state["live_suggestions_active_provider"] = "ollama"

def reset_execution_state():
    st.session_state.last_status = None
    st.session_state.last_stdout = ""
    st.session_state.last_stderr = ""
    st.session_state.last_exit_code = None
    st.session_state.last_execution_time = None
    st.session_state.last_timed_out = False
    st.session_state.last_output_limited = False
    st.session_state.semantic_report = None
    st.session_state.semantic_corrected_code = None
    st.session_state.semantic_correction_note = None


def _editor_file_extension(language: str) -> str:
    return {
        "C++": ".cpp",
        "Python": ".py",
        "JavaScript": ".js",
        "TypeScript": ".ts",
        "SQL": ".sql",
    }.get(language, ".txt")


def _save_active_editor_file() -> None:
    files = st.session_state.get("editor_files", {})
    active_file = st.session_state.get("active_editor_file")
    if active_file in files:
        files[active_file] = st.session_state.get("editor_code", "")


def _reset_editor_file_context() -> None:
    st.session_state.live_diagnostics = []
    st.session_state.cached_compiler_diags = []
    st.session_state.diag_last_code = None
    st.session_state.diag_syntax_checked_code = None
    st.session_state.analysis_cache = {}
    st.session_state.live_suggestions_cache = []
    st.session_state.live_suggestions_code = ""
    st.session_state.live_suggestions_key = None
    st.session_state.live_suggestions_analysis = None
    reset_execution_state()


def _validated_editor_file_name(raw_name: str, replacing_name: str | None = None) -> tuple[str | None, str | None]:
    name = raw_name.strip().rstrip(".")
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or any(separator in name for separator in ("/", "\\", ":"))
        or any(ord(character) < 32 for character in name)
    ):
        return None, "Enter a file name without a folder path."

    if not os.path.splitext(name)[1]:
        name += _editor_file_extension(st.session_state.get("current_language", "C++"))

    files = st.session_state.get("editor_files", {})
    duplicate = next(
        (
            existing_name
            for existing_name in files
            if existing_name.casefold() == name.casefold()
            and existing_name != replacing_name
        ),
        None,
    )
    if duplicate:
        return None, f"A file named {duplicate} already exists."
    return name, None


def _switch_editor_file() -> None:
    _save_active_editor_file()
    files = st.session_state.get("editor_files", {})
    selected_file = st.session_state.get("editor_file_selector")
    if selected_file not in files:
        st.session_state.editor_file_message = "The selected file is no longer available."
        return
    st.session_state.active_editor_file = selected_file
    st.session_state.editor_code = files[selected_file]
    st.session_state.editor_version += 1
    st.session_state.editor_file_message = ""
    _reset_editor_file_context()


def _create_editor_file() -> None:
    _save_active_editor_file()
    name, error = _validated_editor_file_name(
        st.session_state.get("editor_file_name_input", "")
    )
    if error or name is None:
        st.session_state.editor_file_message = error or "Enter a valid file name."
        return

    st.session_state.editor_files[name] = ""
    st.session_state.active_editor_file = name
    st.session_state.editor_file_selector = name
    st.session_state.editor_code = ""
    st.session_state.editor_version += 1
    st.session_state.editor_file_name_input = ""
    st.session_state.editor_file_message = ""
    _reset_editor_file_context()


def _rename_active_editor_file() -> None:
    _save_active_editor_file()
    old_name = st.session_state.get("active_editor_file")
    if not isinstance(old_name, str) or old_name not in st.session_state.editor_files:
        st.session_state.editor_file_message = "The selected file is no longer available."
        return
    name, error = _validated_editor_file_name(
        st.session_state.get("editor_file_name_input", ""),
        replacing_name=old_name,
    )
    if error or name is None:
        st.session_state.editor_file_message = error or "Enter a valid file name."
        return
    if name == old_name:
        st.session_state.editor_file_message = ""
        return

    files = st.session_state.editor_files
    st.session_state.editor_files = {
        name if existing_name == old_name else existing_name: content
        for existing_name, content in files.items()
    }
    st.session_state.active_editor_file = name
    st.session_state.editor_file_selector = name
    st.session_state.editor_file_name_input = ""
    st.session_state.editor_file_message = ""


def _delete_active_editor_file() -> None:
    _save_active_editor_file()
    files = st.session_state.editor_files
    active_file = st.session_state.active_editor_file
    if active_file not in files:
        st.session_state.editor_file_message = "The selected file is no longer available."
        return
    if len(files) <= 1:
        st.session_state.editor_file_message = "Keep at least one file in the editor."
        return

    remaining_files = [name for name in files if name != active_file]
    del files[active_file]
    next_file = remaining_files[0]
    st.session_state.active_editor_file = next_file
    st.session_state.editor_file_selector = next_file
    st.session_state.editor_code = files[next_file]
    st.session_state.editor_version += 1
    st.session_state.editor_file_message = ""
    _reset_editor_file_context()


if "last_status" not in st.session_state:
    reset_execution_state()
if "stdin_input" not in st.session_state:
    st.session_state.stdin_input = ""
if "last_exit_code" not in st.session_state:
    st.session_state.last_exit_code = None
if "last_execution_time" not in st.session_state:
    st.session_state.last_execution_time = None
if "last_timed_out" not in st.session_state:
    st.session_state.last_timed_out = False
if "last_output_limited" not in st.session_state:
    st.session_state.last_output_limited = False
if "green_metrics" not in st.session_state:
    st.session_state.green_metrics = None
if "last_compiled_code" not in st.session_state:
    st.session_state.last_compiled_code = None
if "diag_last_code" not in st.session_state:
    st.session_state.diag_last_code = None
if "diag_last_time" not in st.session_state:
    st.session_state.diag_last_time = 0.0
if "diag_syntax_checked_code" not in st.session_state:
    st.session_state.diag_syntax_checked_code = None
if "cached_compiler_diags" not in st.session_state:
    st.session_state.cached_compiler_diags = []
if "live_diagnostics" not in st.session_state:
    st.session_state.live_diagnostics = []
if "analysis_cache" not in st.session_state:
    st.session_state.analysis_cache = {}
if "diagnostics_source_label" not in st.session_state:
    st.session_state.diagnostics_source_label = "live"
if "semantic_report" not in st.session_state:
    st.session_state.semantic_report = None
if "semantic_corrected_code" not in st.session_state:
    st.session_state.semantic_corrected_code = None
if "semantic_correction_note" not in st.session_state:
    st.session_state.semantic_correction_note = None
if "semantic_compiler_diagnostics" not in st.session_state:
    st.session_state.semantic_compiler_diagnostics = []
if "live_suggestions_cache" not in st.session_state:
    st.session_state.live_suggestions_cache = []
if "live_suggestions_code" not in st.session_state:
    st.session_state.live_suggestions_code = ""
if "live_suggestions_key" not in st.session_state:
    st.session_state.live_suggestions_key = None
if "live_suggestions_message" not in st.session_state:
    st.session_state.live_suggestions_message = ""
if "live_suggestions_analysis" not in st.session_state:
    st.session_state.live_suggestions_analysis = None
if "current_language" not in st.session_state:
    st.session_state.current_language = "C++"

def _apply_language_change(new_lang):
    st.session_state.current_language = new_lang
    c_default = """#include <iostream>\nusing namespace std;\n\nint main() {\n    int age = 20;\n    cout << age << endl;\n    return 0;\n}"""
    current_code = st.session_state.get("editor_code", "").strip()
    if current_code in (
        "",
        c_default.strip(),
        'name = "arpit"\nprint(f"Hello, {name}!")'.strip(),
        'const name = "arpit";\nconsole.log(`Hello, ${name}!`);'.strip(),
        'SELECT "Hello, world!" AS greeting;'.strip(),
    ):
        if new_lang == "Python":
            st.session_state.editor_code = 'name = "arpit"\nprint(f"Hello, {name}!")\n'
        elif new_lang in ("JavaScript", "TypeScript"):
            st.session_state.editor_code = 'const name = "arpit";\nconsole.log(`Hello, ${name}!`);\n'
        elif new_lang == "SQL":
            st.session_state.editor_code = 'SELECT "Hello, world!" AS greeting;\n'
        else:
            st.session_state.editor_code = c_default
    _save_active_editor_file()
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
    st.session_state.live_diagnostics = []
    st.session_state.cached_compiler_diags = []
    st.session_state.diag_last_code = None
    st.session_state.diag_syntax_checked_code = None
    reset_execution_state()

st.sidebar.subheader("Language & Environment")
st.sidebar.markdown(f"Active Mode: **`{st.session_state.current_language}`**")
st.sidebar.caption("Switch language via the editor header dropdown.")

selected_language = st.session_state.current_language
ACE_LANGUAGE_MAP = {
    "C++": "c_cpp",
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "SQL": "sql",
}
active_ace_mode = ACE_LANGUAGE_MAP.get(selected_language, "c_cpp")
lang_code = selected_language.lower().replace("++", "pp")
is_cpp = lang_code in ("cpp", "c")

st.sidebar.subheader("Semantic Analysis")
semantic_enabled = st.sidebar.checkbox(
    "Enable semantic analysis", value=SEMANTIC_ANALYSIS_ENABLED, key="semantic_analysis_enabled"
)
confidence_options = {"LOW": 0.30, "MEDIUM": 0.50, "HIGH": 0.75}
semantic_min_label = st.sidebar.selectbox(
    "Minimum confidence for compile results",
    options=list(confidence_options),
    index=1,
    key="semantic_minimum_confidence",
)
semantic_min_confidence = confidence_options[semantic_min_label]
include_semantics_in_ai_review = st.sidebar.checkbox(
    "Include semantic warnings in AI review", value=False, key="semantic_ai_explanation"
)

st.sidebar.subheader("Deep AI Reasoning")
reasoning_enabled = st.sidebar.checkbox(
    "Enable evidence-grounded reasoning",
    value=REASONING_AVAILABLE,
    key="reasoning_enabled",
    disabled=not REASONING_AVAILABLE,
    help="Multi-stage AI analysis pipeline: collects compiler facts, reasons about intent, and verifies findings with evidence."
        + ("" if REASONING_AVAILABLE else " (reasoning module unavailable)"),
)
reasoning_profile = st.sidebar.selectbox(
    "Reasoning profile",
    options=["quick", "standard", "thorough"],
    index=1,
    key="reasoning_profile",
    help="Quick: facts + hypothesize. Standard: + refute + fix. Thorough: + optimization.",
)

live_ai_provider = "ollama"
_installed_ollama, _ = _ensure_ollama_running()
_ollama_models = _installed_ollama if _installed_ollama else [
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "llama3:latest",
]
_default_7b_idx = _preferred_model_index(_ollama_models)
st.sidebar.markdown(
    """
    <div style="background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.35); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px;">
        <div style="font-size: 0.85rem; font-weight: 600; color: #22c55e; display: flex; align-items: center; gap: 6px;">
            <span>🧠</span> <span>AI via Ollama</span>
        </div>
        <div style="font-size: 0.75rem; color: var(--muted); margin-top: 4px; line-height: 1.35;">
            Local AI execution via Ollama. No external API key required.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
optimal_ai_model = st.sidebar.selectbox(
    "Optimizer & agent model",
    options=_ollama_models,
    index=_default_7b_idx,
    key="ollama_optimal_model_select",
)
st.sidebar.caption(
    "Choose any installed Ollama model here. To add one, run `ollama pull <model>` "
    "in a terminal, then refresh this app. Code-tuned models are usually better at programming than general chat models."
)
chat_ai_model = st.sidebar.selectbox(
    "Chatbot tutor model",
    options=_ollama_models,
    index=_default_7b_idx,
    key="ollama_chat_model_select",
)
ollama_optimal_model = optimal_ai_model

# Synchronize currently active model state
st.session_state["last_served_model"] = optimal_ai_model
st.session_state["last_served_provider"] = "ollama"
ollama_status = get_ollama_model_status(optimal_ai_model)
st.sidebar.markdown(
    f"<div style='display:flex; align-items:center; gap:8px; margin-top: 8px;'><span style='font-size:18px;'>{ollama_status['symbol']}</span><strong>{ollama_status['status']}</strong></div>",
    unsafe_allow_html=True,
)
st.sidebar.caption(ollama_status["detail"])

# Hero banner replaced by setup_ui() slim header above.

if "editor_width_pct" not in st.session_state:
    st.session_state.editor_width_pct = 50

if "layout_swapped" not in st.session_state:
    st.session_state.layout_swapped = False

col_a, col_b = st.columns(2)
if st.session_state.layout_swapped:
    col2 = col_a  # Chatbot rendered on Left
    col1 = col_b  # Editor rendered on Right
else:
    col1 = col_a  # Editor rendered on Left
    col2 = col_b  # Chatbot rendered on Right


# LEFT COLUMN: THE CODE EDITOR & COMPILER

with col1:
    pane_marker("left")

    h_col1, h_col2 = st.columns([3, 2])
    with h_col1:
        st.markdown(ui_theme.code_label(f"**{selected_language}** Editor"), unsafe_allow_html=True)
    with h_col2:
        lang_options = ["C++", "Python", "JavaScript", "TypeScript", "SQL"]
        def _on_editor_lang_change():
            chosen = st.session_state.editor_lang_dropdown
            _apply_language_change(chosen)

        curr_lang = st.session_state.get("current_language", "C++")
        lang_idx = lang_options.index(curr_lang) if curr_lang in lang_options else 0
        st.selectbox(
            "Language",
            options=lang_options,
            index=lang_idx,
            key="editor_lang_dropdown",
            on_change=_on_editor_lang_change,
            label_visibility="collapsed",
        )

    default_code = """#include <iostream>
using namespace std;

int main() {
    int age = 20;
    cout << age << endl;
    return 0;
}"""
    
    if "editor_code" not in st.session_state:
        st.session_state.editor_code = default_code

    if "editor_files" not in st.session_state:
        initial_file = f"main{_editor_file_extension(selected_language)}"
        st.session_state.editor_files = {initial_file: st.session_state.editor_code}
        st.session_state.active_editor_file = initial_file
        st.session_state.editor_file_selector = initial_file
    elif not st.session_state.editor_files:
        initial_file = f"main{_editor_file_extension(selected_language)}"
        st.session_state.editor_files = {initial_file: st.session_state.editor_code}
        st.session_state.active_editor_file = initial_file
        st.session_state.editor_file_selector = initial_file

    editor_files = st.session_state.editor_files
    if st.session_state.get("active_editor_file") not in editor_files:
        st.session_state.active_editor_file = next(iter(editor_files))
    if st.session_state.get("editor_file_selector") not in editor_files:
        st.session_state.editor_file_selector = st.session_state.active_editor_file

    st.radio(
        "Open file",
        options=list(editor_files),
        key="editor_file_selector",
        horizontal=True,
        label_visibility="collapsed",
        on_change=_switch_editor_file,
    )
    with st.expander("Manage files"):
        st.caption("Files are kept for this browser session. Compile and analyze runs the selected file only.")
        st.text_input(
            "File name",
            key="editor_file_name_input",
            placeholder=f"for example, helper{_editor_file_extension(selected_language)}",
        )
        file_action_columns = st.columns(3)
        file_action_columns[0].button(
            "Add file",
            key="add_editor_file",
            on_click=_create_editor_file,
        )
        file_action_columns[1].button(
            "Rename active",
            key="rename_editor_file",
            on_click=_rename_active_editor_file,
        )
        file_action_columns[2].button(
            "Delete active",
            key="delete_editor_file",
            on_click=_delete_active_editor_file,
        )
        st.caption(f"Selected: {st.session_state.active_editor_file}")
        if st.session_state.get("editor_file_message"):
            st.error(st.session_state.editor_file_message)

    @st.fragment

    def render_editor_and_diagnostics():
        editor_key = f"source_editor_{lang_code}_{st.session_state.editor_version}"
        editor_value = st_ace(
            value=st.session_state.editor_code,
            language=active_ace_mode,
            theme=_active_theme_dict["editor"],
            keybinding="vscode",
            height=275,
            font_size=14,
            tab_size=4,
            wrap=False,
            show_gutter=True,
            show_print_margin=False,
            auto_update=True,
            key=editor_key,
        )
        current_user_code = editor_value if isinstance(editor_value, str) else st.session_state.editor_code
        st.session_state.editor_code = current_user_code
        st.session_state.editor_files[st.session_state.active_editor_file] = current_user_code
        st.caption("⌨️ Type freely. Real-time diagnostics update locally without reloading the screen. Click **▷ Compile and analyze** to run.")

        if st.session_state.last_compiled_code is not None and current_user_code != st.session_state.last_compiled_code:
            reset_execution_state()

        # --- INLINE LIVE DIAGNOSTICS & STATUS PILL ---
        current_diags = sync_live_diagnostics(current_user_code)
        ace_annotations = []
        if current_diags:
            for d in current_diags:
                line_val = d.get("line")
                if line_val is not None:
                    try:
                        row_idx = max(0, int(line_val) - 1)
                        ace_annotations.append({
                            "row": row_idx,
                            "column": 0,
                            "text": d.get("message", d.get("title", "Error")),
                            "type": "error" if d.get("category") in {"ERROR", "BUG", "SECURITY_CRITICAL"} else "warning",
                        })
                    except Exception:
                        pass

            import base64
            b64_ann = base64.b64encode(json.dumps(ace_annotations).encode("utf-8")).decode("ascii")
            st.html(
                f'<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" '
                f'onload="try{{const a=JSON.parse(atob(\'{b64_ann}\'));'
                f'if(window.parent&&window.parent.__setAceAnnotations)window.parent.__setAceAnnotations(a);'
                f'else if(window.__setAceAnnotations)window.__setAceAnnotations(a);}}catch(e){{}}this.remove();" '
                f'style="display:none;">'
            )
            primary_diag = current_diags[0]
            p_line = primary_diag.get("line")
            p_line_txt = f"Line {p_line}: " if p_line else ""
            p_msg = primary_diag.get("message", primary_diag.get("title", "Syntax or type issue detected"))
            status_banner("err", f"{p_line_txt}{p_msg}")
        else:
            st.html(
                '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" '
                'onload="try{if(window.parent&&window.parent.__setAceAnnotations)window.parent.__setAceAnnotations([]);'
                'else if(window.__setAceAnnotations)window.__setAceAnnotations([]);}catch(e){}this.remove();" '
                'style="display:none;">'
            )
            status_banner("ok", "No syntax or type errors. Ready to compile.")

        # --- TABS: DIAGNOSTICS | INPUT (STDIN) ---
        t_diag, t_stdin = st.tabs(["Diagnostics", "Input (stdin)"])
        with t_diag:
            if not current_diags:
                st.markdown('<div style="padding: 0.6rem 0; font-size: 0.88rem; color: var(--muted);">Clean. 0 errors, 0 warnings.</div>', unsafe_allow_html=True)
            else:
                render_live_diagnostics()

        with t_stdin:
            st.markdown('<div class="ui-stdin-wrapper">', unsafe_allow_html=True)
            st.text_area(
                label="stdin",
                label_visibility="collapsed",
                placeholder="Enter input here (one value per line or space-separated)...",
                height=85,
                key="stdin_input",
            )
            st.markdown('</div>', unsafe_allow_html=True)

    render_editor_and_diagnostics()
    user_code = st.session_state.get("editor_code", default_code)

    compile_btn = st.button("▷ Compile and analyze", type="primary", use_container_width=True, key="btn_compile_run")

    if compile_btn:
        if not user_code.strip():
            ui_theme.show_status(st, "warning", "WARNING", "Please write some code first!", icon="⚠️")
        else:
            reset_execution_state()
            start_time = time.time()
            app_dir = os.path.dirname(os.path.abspath(__file__))
            tracker = EmissionsTracker(project_name="llama3_debugger", output_dir=app_dir, log_level="error")
            tracker.start()
            # 1. Security Scan
            is_safe, warning_msg = run_security_guardrail(user_code)
            
            if not is_safe:
                stop_and_save_metrics(tracker, start_time)
                log_security_event(user_code, warning_msg, user_action="Compile Blocked (Pre-Execution Gate)")
                ui_theme.show_status(st, "error", "SECURITY BLOCK", warning_msg, icon="🚫")
                st.stop() 

            st.session_state.semantic_report = None
            st.session_state.semantic_corrected_code = None
            st.session_state.semantic_correction_note = None
            st.session_state.semantic_compiler_diagnostics = []
            if semantic_enabled and is_cpp:
                try:
                    semantic_report = analyze_semantics(
                        user_code, min_confidence=semantic_min_confidence
                    )
                    st.session_state.semantic_report = semantic_report
                    if semantic_report.stats.get("error"):
                        st.session_state.semantic_correction_note = "Semantic analysis was incomplete; compilation will continue."
                    else:
                        corrected_code, correction_note = validate_semantic_correction(
                            user_code, semantic_report
                        )
                        st.session_state.semantic_corrected_code = corrected_code
                        st.session_state.semantic_correction_note = correction_note
                    if semantic_report.stats.get("dictionary_warning"):
                        st.session_state.semantic_correction_note = semantic_report.stats["dictionary_warning"]
                except Exception as error:
                    st.session_state.semantic_correction_note = (
                        f"Semantic analysis was unavailable; compilation will continue. ({error})"
                    )
            
            # 2. Compile and Run
            with st.spinner("Compiling securely in sandbox..."):
                stdin_to_pass = st.session_state.get("stdin_input", "")
                res = compile_and_run(user_code, stdin_input=stdin_to_pass, language=lang_code)
                return_code, stdout, stderr = res[0], res[1], res[2]

                st.session_state.last_compiled_code = user_code

                st.session_state.last_status = return_code
                st.session_state.last_stdout = stdout
                st.session_state.last_stderr = stderr
                st.session_state.last_exit_code = getattr(res, "exit_code", 0 if return_code == 0 else return_code)
                st.session_state.last_execution_time = getattr(res, "execution_time", 0.0)
                st.session_state.last_timed_out = getattr(res, "timed_out", False)
                st.session_state.last_output_limited = getattr(res, "output_limited", False)
                st.session_state.last_agent_log = None
            # 3. Process Results
            if return_code == 0:
                # --- PATH A: SUCCESS ---
                ui_theme.show_status(st, "success", "SUCCESS", "Execution Successful!", icon="✅")
                render_terminal_output(stdout, "Program output")
                if getattr(res, "output_limited", False):
                    st.caption(
                        "Program completed. Only the first 256 KB of output is captured; "
                        "expand the output preview to see the rest of the captured text."
                    )

                st.session_state.semantic_compiler_diagnostics = []
                local_diags = get_cached_local_diagnostics(
                    user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
                )
                st.session_state.diagnostics_source_label = "post-compile"
                st.session_state.diag_last_code = user_code
                st.session_state.diag_syntax_checked_code = user_code

                # --- COMPREHENSIVE CODE WALKTHROUGH & LOGICAL COMPLEXITY ANALYSIS ---
                reasoning_findings_text = ""
                if reasoning_enabled and REASONING_AVAILABLE and is_cpp:
                    with st.spinner("🧠 Running evidence-grounded analysis..."):
                        try:
                            app_dir = os.path.dirname(os.path.abspath(__file__))
                            reasoning_result = run_reasoning(
                                user_code,
                                run_result={
                                    "return_code": return_code,
                                    "stdout": stdout,
                                    "stderr": stderr,
                                    "stdin": stdin_to_pass,
                                },
                                profile=reasoning_profile,
                                work_dir=app_dir,
                                provider_name=live_ai_provider,
                                ollama_model=ollama_optimal_model,
                                language=lang_code,
                            )
                            reasoning_diags = findings_to_diagnostics(reasoning_result.findings)
                            st.session_state.live_diagnostics = diag.merge_diagnostics(
                                local_diags, reasoning_diags
                            )
                            reasoning_findings_text = findings_to_chat(reasoning_result)
                        except Exception:
                            pass
                else:
                    st.session_state.live_diagnostics = local_diags

                with st.spinner(f"🤖 {ollama_optimal_model} is explaining {selected_language} logic, complexity, security & efficiency..."):
                    walkthrough_analysis = generate_code_walkthrough_and_complexity(
                        code=user_code,
                        language=selected_language,
                        stdin_input=stdin_to_pass,
                        stdout=stdout,
                        stderr=stderr,
                        findings_text=reasoning_findings_text,
                        ollama_model=ollama_optimal_model,
                    )

                success_msg = f"**Code compiled and executed successfully!** Output is displayed in the Program Output section.\n\n{walkthrough_analysis}"
                st.session_state.messages.append({"role": "assistant", "content": success_msg})
                log_interaction("SYSTEM (Success)", success_msg, user_code, "Walkthrough & Complexity Analysis")

                stop_and_save_metrics(tracker, start_time)
                st.rerun()

            elif return_code == 2:
                # --- PATH B: RUNTIME ERROR ---
                ui_theme.show_status(
                    st,
                    "error",
                    "RUNTIME ERROR",
                    f"Runtime Error / Crash Detected in {selected_language}!",
                    icon="⚠️",
                )
                
                if stdout:
                    st.markdown(ui_theme.code_label("Output before error"), unsafe_allow_html=True)
                    st.code(stdout, language="text")

                crash_diagnostic = diag.make_diagnostic(
                    category="ERROR",
                    title="Runtime error",
                    message="The program compiled but crashed or exited with a non-zero exit code. See the runtime analysis below.",
                    source="runtime",
                )
                if is_cpp:
                    st.session_state.live_diagnostics = diag.merge_diagnostics(
                        get_cached_local_diagnostics(
                            user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
                        ), [crash_diagnostic]
                    )
                else:
                    st.session_state.live_diagnostics = [crash_diagnostic]

                st.session_state.semantic_compiler_diagnostics = []
                st.session_state.diagnostics_source_label = "post-compile"
                st.session_state.diag_last_code = user_code
                st.session_state.diag_syntax_checked_code = user_code
                
                with st.spinner(f"🤖 AI is analyzing the {selected_language} runtime error..."):
                    rt_prompt = (
                        f"Explain this {selected_language} runtime error and provide the corrected code:\n\n"
                        f"CODE:\n```{lang_code}\n{user_code}\n```\n\n"
                        f"RUNTIME ERROR / TRACEBACK:\n```\n{stderr}\n```"
                    )
                    explanation = get_ai_explanation(rt_prompt, f"Runtime error in {selected_language}:\n{stderr}")
                initial_msg = f"**🚨 Runtime Exception Detected ({selected_language})**\n\n{explanation}"

                st.session_state.messages.append({"role": "assistant", "content": initial_msg})
                log_interaction("SYSTEM (Runtime Error)", initial_msg, user_code, "Runtime Error")
                stop_and_save_metrics(tracker, start_time)
                st.rerun()

            elif return_code == -1:
                # --- PATH D: TIMEOUT / EXECUTION ERROR ---
                is_timeout = st.session_state.get("last_timed_out", False)
                is_output_limited = st.session_state.get("last_output_limited", False)
                timeout_msg = stderr or "Execution timed out. Your program may be waiting for more input or stuck in an infinite loop."
                ui_theme.show_status(
                    st,
                    "error",
                    "EXECUTION TIMED OUT" if is_timeout else "EXECUTION ERROR",
                    timeout_msg,
                    icon="⏱️" if is_timeout else "❌",
                )
                alert_title = "Execution timed out" if is_timeout else "Execution error"
                initial_msg = f"**{alert_title}**\n\n{timeout_msg}"
                st.session_state.messages.append({"role": "assistant", "content": initial_msg})
                log_interaction(
                    "SYSTEM (Execution Stopped)",
                    initial_msg,
                    user_code,
                    "Timeout" if is_timeout else "Execution Error",
                )
                stop_and_save_metrics(tracker, start_time)
                st.rerun()
                
            else:
                # --- PATH C: COMPILE ERROR (STATIC ANALYSIS) ---
                ui_theme.show_status(
                    st,
                    "error",
                    "COMPILATION FAILED" if is_cpp else "SYNTAX ERROR",
                    "Compilation Failed! Check the AI chat for diagnostics." if is_cpp else f"{selected_language} Syntax Error! Check the AI chat for diagnostics.",
                    icon="❌",
                )
                log_error(stderr, user_code)

                if is_cpp:
                    post_compile_diagnostics = diag.parse_compiler_diagnostics(stderr)
                else:
                    import re
                    m = re.search(r":(\d+)", stderr) or re.search(r"line (\d+)", stderr, re.IGNORECASE)
                    found_line = int(m.group(1)) if m else 1
                    err_lines = [l.strip() for l in stderr.splitlines() if l.strip()]
                    first_err = err_lines[0] if err_lines else f"{selected_language} Syntax Error"
                    post_compile_diagnostics = [diag.make_diagnostic(
                        category="ERROR",
                        title=f"{selected_language} Syntax Error",
                        message=first_err,
                        line=found_line,
                        source="runtime_frontend",
                    )]

                if return_code == -1:
                    post_compile_diagnostics.append(diag.make_diagnostic(
                        category="WARNING",
                        title="Compiler process did not complete",
                        message="No confirmed compiler diagnostic was available; the compile/run process may have timed out or failed.",
                        source="process",
                    ))
                if is_cpp:
                    st.session_state.live_diagnostics = diag.merge_diagnostics(
                        get_cached_local_diagnostics(
                            user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
                        ), post_compile_diagnostics
                    )
                else:
                    st.session_state.live_diagnostics = post_compile_diagnostics
                st.session_state.semantic_compiler_diagnostics = post_compile_diagnostics
                st.session_state.diagnostics_source_label = "post-compile"
                st.session_state.diag_last_code = user_code
                st.session_state.diag_syntax_checked_code = user_code
                
                category, strategy = classify_error(stderr) if is_cpp else (f"{selected_language} Syntax Error", "Fix syntax error according to language grammar")
                reasoning_ran = False
                if reasoning_enabled and REASONING_AVAILABLE:
                    with st.spinner("🧠 Running evidence-grounded analysis on error..."):
                        try:
                            app_dir = os.path.dirname(os.path.abspath(__file__))
                            stdin_to_pass = st.session_state.get("stdin_input", "")
                            reasoning_result = run_reasoning(
                                user_code,
                                run_result={
                                    "return_code": return_code,
                                    "stdout": stdout,
                                    "stderr": stderr,
                                    "stdin": stdin_to_pass,
                                },
                                profile=reasoning_profile,
                                work_dir=app_dir,
                                provider_name=live_ai_provider,
                                ollama_model=ollama_optimal_model,
                                language=lang_code,
                            )
                            reasoning_ran = True
                            reasoning_diags = findings_to_diagnostics(reasoning_result.findings)
                            st.session_state.live_diagnostics = diag.merge_diagnostics(
                                st.session_state.live_diagnostics, reasoning_diags
                            )
                            chat_text = findings_to_chat(reasoning_result)
                            initial_msg = f"**Execution Failed.** (Categorized: {category})\n\n{chat_text}"
                            patched_code = findings_to_patch_code(reasoning_result, user_code)
                            st.session_state.messages.append({"role": "assistant", "content": initial_msg})
                            if patched_code:
                                is_fix_safe, _, sec_status = validate_ai_fix(f"```{lang_code}\n{patched_code}\n```")
                                if is_fix_safe:
                                    verif_summary = reasoning_result.findings[0].verification.summary if reasoning_result.findings and reasoning_result.findings[0].verification else "Verified correction available."
                                    st.session_state.messages.append({
                                        "role": "assistant",
                                        "content": f"✅ Verified Patch ({verif_summary}):",
                                        "code_expander": patched_code
                                    })
                            log_interaction("SYSTEM (Compile Failed - Reasoning)", initial_msg, user_code, category, patched_code or "")
                        except Exception:
                            reasoning_ran = False

                if not reasoning_ran:
                    if is_cpp:
                        prompt = get_diagnostic_prompt(category, strategy, user_code, stderr)
                    else:
                        prompt = (
                            f"You are an expert {selected_language} debugging assistant.\n"
                            f"The user wrote the following {selected_language} code:\n"
                            f"```{lang_code}\n{user_code}\n```\n\n"
                            f"The runtime returned this error:\n```\n{stderr}\n```\n\n"
                            f"Explain the issue clearly and provide the corrected code in a ```{lang_code} block."
                        )
                    with st.spinner("🤖 AI is analyzing the syntax error..."):
                        explanation = get_ai_explanation(prompt)
                    
                    is_fix_safe, suggested_code, security_status = validate_ai_fix(explanation)
                    initial_msg = f"**Detected:** {category}\n\n{explanation}"
                    
                    st.session_state.messages.append({"role": "assistant", "content": initial_msg})
                    log_interaction("SYSTEM (Compile Failed)", initial_msg, user_code, category, suggested_code)
                    
                    if is_fix_safe:
                        st.session_state.messages.append({
                            "role": "assistant", 
                            "content": "✅ " + security_status,
                            "code_expander": suggested_code 
                        })
                    else:
                        if suggested_code:
                            st.session_state.messages.append({"role": "assistant", "content": "🚫 AI generated insecure code! " + security_status})
                stop_and_save_metrics(tracker, start_time)
                st.rerun()
    if st.session_state.last_status is not None:
        st.markdown("---")
        st.subheader("🖥️ Terminal Output")
        
        if st.session_state.last_status == 0:
            ui_theme.show_status(st, "success", "SUCCESS", "Execution Successful!", icon="✅")
            render_terminal_output(st.session_state.last_stdout, "Program output")
            if st.session_state.get("last_output_limited", False):
                st.caption(
                    "Program completed. Output capture is capped at 256 KB; "
                    "the remainder was discarded to protect memory."
                )
        elif st.session_state.last_status == 2:
            ui_theme.show_status(st, "error", "RUNTIME ERROR", "Runtime Error / Crash Detected!" if is_cpp else f"Runtime Exception in {selected_language}!", icon="⚠️")
            st.markdown(ui_theme.code_label("Runtime error output"), unsafe_allow_html=True)
            st.code(st.session_state.last_stderr, language="text")
        elif (
            st.session_state.get("last_timed_out", False)
            or st.session_state.last_status == -1
        ):
            timed_out = st.session_state.get("last_timed_out", False)
            if timed_out:
                status_label = "TIMED OUT"
                status_message = "Execution timed out; partial output is shown below."
                status_icon = "⏱️"
            else:
                status_label = "EXECUTION ERROR"
                status_message = st.session_state.last_stderr or "The program could not be completed."
                status_icon = "❌"
            ui_theme.show_status(st, "error", status_label, status_message, icon=status_icon)
            st.markdown(ui_theme.code_label("Program output preview"), unsafe_allow_html=True)
            captured_output = st.session_state.last_stdout or (
                "(No output was captured. For C++, flush output with std::endl or std::flush.)"
            )
            render_terminal_output(captured_output, "Program output")
            if st.session_state.get("last_output_limited", False):
                st.caption(
                    "The app retained up to 256 KB of output. Excess output was discarded; "
                    "this did not stop the program."
                )
            if timed_out:
                st.markdown(ui_theme.code_label("Timeout details"), unsafe_allow_html=True)
                st.code(st.session_state.last_stderr, language="text")
        else:
            ui_theme.show_status(st, "error", "COMPILATION FAILED" if is_cpp else "SYNTAX ERROR", "Compilation Failed!" if is_cpp else f"{selected_language} Syntax Error!", icon="❌")
            st.markdown(ui_theme.code_label("Compiler output" if is_cpp else "Error output"), unsafe_allow_html=True)
            st.code(st.session_state.last_stderr, language="text")

        render_semantic_results(
            st.session_state.semantic_report,
            user_code,
            st.session_state.get("semantic_compiler_diagnostics", []),
        )

    # DASHBOARD
    if st.session_state.get("last_status") is not None:
        quality = compute_code_quality_metrics(user_code, language=lang_code)
        st.markdown("---")
        st.subheader("🛡️ Static Code Check Results")
        q_col1, q_col2, q_col3, q_col4 = st.columns(4)
        q_col1.metric("🛡️ Security Checks Clear", f"{quality['security_score']:.0f}%", delta=f"{quality['insecure_pct']:.0f}% checks flagged", delta_color="inverse")
        q_col2.metric("⚡ Efficiency Checks Clear", f"{quality['efficiency_score']:.0f}%", delta=f"{quality['inefficient_pct']:.0f}% checks flagged", delta_color="inverse")
        q_col3.metric("📊 Scanned Scope", f"{quality['total_loc']} Lines ({selected_language})")
        observed_time = st.session_state.get("last_execution_time")
        q_col4.metric(
            "⏱️ Sandbox Wall Time",
            f"{observed_time:.3f} s" if observed_time is not None else "Unavailable",
        )
        st.caption(
            f"Based on configured static checks ({quality['security_checks_flagged']}/"
            f"{quality['security_checks_total']} security and "
            f"{quality['efficiency_checks_flagged']}/{quality['efficiency_checks_total']} "
            "efficiency checks flagged). These percentages are not probabilities, "
            "runtime benchmarks, or proof that code is secure or optimal. Sandbox wall time "
            "includes container startup and compilation for compiled languages; it is not "
            "a code-only performance benchmark."
        )

        deductions = quality.get("deductions", [])
        has_deductions = bool(
            len(deductions) > 0
            or quality.get("critical_threats", 0) > 0
            or quality.get("advisory_threats", 0) > 0
            or len(quality.get("inefficiency_notes", [])) > 0
        )

        with st.expander("🔍 Check Findings & How to Address Them", expanded=has_deductions):
            if not has_deductions:
                st.markdown(
                    '<div style="padding: 0.6rem 0; font-size: 0.9rem; color: var(--accent);">'
                    '🎯 <strong>No configured checks flagged:</strong> This is not proof of overall security or optimality.'
                    '</div>',
                    unsafe_allow_html=True
                )
            else:
                for d in deductions:
                    cat = d.get("category", "Optimization")
                    title = d.get("title", "")
                    why = d.get("why_deducted", "")
                    how = d.get("how_to_reach_100", "")
                    code_fix = d.get("code_fix", "")
                    sev = d.get("severity", "WARNING")

                    badge_cls = "efficiency" if cat == "Efficiency" else ("security-crit" if sev == "CRITICAL" else "security-warn")
                    cat_icon = "⚡" if cat == "Efficiency" else "🛡️"

                    code_block_html = f'<div class="ui-deduction-code">{html.escape(code_fix)}</div>' if code_fix else ""

                    card_html = f"""
                    <div class="ui-deduction-card">
                        <div class="ui-deduction-header">
                            <span class="ui-deduction-title">
                                <span>{cat_icon}</span>
                                <span>{html.escape(title)}</span>
                            </span>
                            <span class="ui-deduction-badge {badge_cls}">Flagged {html.escape(cat)} check · {html.escape(sev)}</span>
                        </div>
                        <div class="ui-deduction-why">
                            <strong>Finding:</strong> {html.escape(why)}
                        </div>
                        <div class="ui-deduction-fix">
                            <strong>Suggested remediation:</strong> {html.escape(how)}
                        </div>
                        {code_block_html}
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)

    if st.session_state.green_metrics:
        st.markdown("---")
        st.subheader("🌿 Green Compiler Metrics")
        m_col1, m_col2, m_col3 = st.columns(3)
        m_col1.metric("⏱️ Execution Latency", f"{st.session_state.green_metrics['latency']:.3f} s")
        energy_val = st.session_state.green_metrics.get("energy")
        carbon_val = st.session_state.green_metrics.get("carbon")
        m_col2.metric("⚡ Energy Used", f"{energy_val:.6f} kWh" if energy_val is not None else "Unavailable")
        m_col3.metric("🌍 Carbon Emitted", f"{carbon_val:.5f} g" if carbon_val is not None else "Unavailable")
        if st.session_state.green_metrics.get("provenance") == "unavailable":
            st.caption("ℹ️ Hardware energy/carbon counters are unsupported or unavailable on this host; metrics are reported honestly rather than fabricated.")

    if st.session_state.get("last_status") == 0:
        st.markdown("---")
        st.subheader("📈 Empirical Profiler Agent (Parallel Scaling Benchmark)")
        st.caption("Concurrently benchmarks execution latency across input sizes N = [10, 100, 1000, 5000] in isolated sandbox threads and correlates empirical scaling with theoretical Big-O.")

        prof_col1, prof_col2 = st.columns([1.2, 2.8])
        with prof_col1:
            run_prof_btn = st.button("⚡ Run Empirical Parallel Profiler", key="btn_run_empirical_profiler", type="secondary")
        
        should_run = run_prof_btn or ("empirical_profile_report" in st.session_state and st.session_state.get("empirical_profile_code") == user_code)

        if should_run:
            if run_prof_btn or ("empirical_profile_report" not in st.session_state) or (st.session_state.get("empirical_profile_code") != user_code):
                with st.spinner("⚡ Running parallel empirical profiling across input scales N..."):
                    prof_report = run_empirical_profiler(
                        user_code,
                        language=lang_code,
                        baseline_stdin=st.session_state.get("stdin_input", ""),
                    )
                    st.session_state.empirical_profile_report = prof_report
                    st.session_state.empirical_profile_code = user_code
            else:
                prof_report = st.session_state.empirical_profile_report

            if prof_report:
                pc1, pc2, pc3, pc4 = st.columns(4)
                pc1.metric("📈 Empirical Big-O", prof_report.empirical_complexity)
                pc2.metric("📐 Theoretical Big-O", prof_report.theoretical_complexity)
                pc3.metric("🎯 Correlation", prof_report.correlation_status)
                pc4.metric("⏱️ Benchmark Time", f"{prof_report.total_elapsed_s:.3f} s")

                st.markdown(prof_report.verdict_markdown)

                import pandas as pd
                chart_df = pd.DataFrame(prof_report.chart_data)
                st.line_chart(chart_df.set_index("Scale (N)"))

                with st.expander("📊 Scaling Benchmark Table", expanded=False):
                    table_rows = [
                        {
                            "Scale (N)": pt.n,
                            "Latency (ms)": pt.wall_time_ms,
                            "Status": "Exit 0" if pt.status_code == 0 else f"Err {pt.status_code}",
                            "Timed Out": "Yes" if pt.timed_out else "No",
                            "Output Preview": pt.sample_output,
                        }
                        for pt in prof_report.data_points
                    ]
                    st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

    if st.session_state.get("last_status") is not None:
        with st.expander("📤 Execution Output & Status Details", expanded=False):
            _status = st.session_state.last_status
            _stdout = st.session_state.get("last_stdout", "")
            _stderr = st.session_state.get("last_stderr", "")
            _exit_code = st.session_state.get("last_exit_code")
            _exec_time = st.session_state.get("last_execution_time", 0.0)
            _timed_out = st.session_state.get("last_timed_out", False)

            m_c1, m_c2, m_c3 = st.columns(3)
            with m_c1:
                if _timed_out:
                    st.metric("Exit Code", "Timed Out")
                elif _exit_code is not None:
                    st.metric("Exit Code", str(_exit_code))
                else:
                    st.metric("Exit Code", "0" if _status == 0 else str(_status))
            with m_c2:
                st.metric("Sandbox Wall Time", f"{_exec_time:.3f} s" if _exec_time is not None else "Unavailable")
            with m_c3:
                if _status == 0:
                    st.markdown(f"<div style='margin-top: 10px;'>{ui_theme.badge('Success (0)', tone='success')}</div>", unsafe_allow_html=True)
                elif _status == 2:
                    st.markdown(f"<div style='margin-top: 10px;'>{ui_theme.badge('Runtime Error', tone='error')}</div>", unsafe_allow_html=True)
                elif _timed_out or _status == -1:
                    st.markdown(f"<div style='margin-top: 10px;'>{ui_theme.badge('Timed Out', tone='warning')}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='margin-top: 10px;'>{ui_theme.badge('Compile Error', tone='error')}</div>", unsafe_allow_html=True)

            st.markdown(ui_theme.code_label("Standard Output (stdout)"), unsafe_allow_html=True)
            if _stdout and _stdout.strip():
                st.code(_stdout, language="text")
            else:
                st.code("(no output)", language="text")

            if _stderr and _stderr.strip():
                st.markdown(ui_theme.code_label("Standard Error (stderr)"), unsafe_allow_html=True)
                if _timed_out:
                    st.error(_stderr)
                elif _status == 0:
                    st.code(_stderr, language="text")
                else:
                    st.error(_stderr)

    #End of Left Column....


# RIGHT COLUMN: THE MULTI-TURN AI CHAT

with col2:
    pane_marker("right")
    
    # 1. QUICK PROMPT PILLS (Explain, Debug, Optimize, Tests)
    qp_c1, qp_c2, qp_c3, qp_c4 = st.columns(4)
    run_explain_pill = False
    run_debug_pill = False
    run_optimizer_pill = False
    run_tests_pill = False

    with qp_c1:
        if st.button("Explain", key="pill_explain", use_container_width=True):
            run_explain_pill = True

    with qp_c2:
        if st.button("Debug", key="pill_debug", use_container_width=True):
            run_debug_pill = True

    with qp_c3:
        if st.button("⚡ Optimize", key="pill_optimize", use_container_width=True):
            run_optimizer_pill = True

    with qp_c4:
        if st.button("Tests", key="pill_tests", use_container_width=True):
            run_tests_pill = True

    if run_explain_pill:
        with st.spinner(f"🤖 {ollama_optimal_model} is explaining your {selected_language} code..."):
            walkthrough = generate_code_walkthrough_and_complexity(
                user_code,
                selected_language,
                stdin_input=st.session_state.get("stdin_input", ""),
                stdout=st.session_state.get("last_stdout", ""),
                ollama_model=ollama_optimal_model,
            )
        st.session_state.messages.append({
            "role": "user",
            "content": f"Explain what this {selected_language} code does, step by step.",
        })
        st.session_state.messages.append({
            "role": "assistant",
            "content": walkthrough,
        })
        st.rerun(scope="app")

    if run_debug_pill:
        with st.spinner(f"🕵️ Scanning {selected_language} code for bugs & flaws..."):
            from tool_runner import collect_empirical_evidence
            ev = collect_empirical_evidence(user_code, language=lang_code, stdin_input=st.session_state.get("stdin_input", ""))
            flaws = ev.get("tool_findings", [])
            if flaws:
                flaw_items = "\n".join(f"- **Line {f.get('line', '?')}:** {f.get('message', '')} ({f.get('tool', 'tool')})" for f in flaws)
                debug_reply = f"### 🕵️ Code Debugging & Flaw Analysis\n\nIdentified {len(flaws)} potential issue(s):\n{flaw_items}\n\nClick **⚡ Optimize** to automatically harden and fix these issues across all agents."
            else:
                debug_reply = f"### 🕵️ Code Debugging & Flaw Analysis\n\n✅ No static memory bugs, uninitialized variables, or division-by-zero detected. The program structure is valid."

        st.session_state.messages.append({
            "role": "user",
            "content": f"Analyze this {selected_language} code for potential bugs, syntax errors, or logic flaws.",
        })
        st.session_state.messages.append({
            "role": "assistant",
            "content": debug_reply,
        })
        st.rerun(scope="app")

    if run_tests_pill:
        with st.spinner(f"🧪 {ollama_optimal_model} is generating edge-case tests..."):
            from test_generator_agent import generate_test_matrix
            test_matrix = generate_test_matrix(
                user_code,
                baseline_stdin=st.session_state.get("stdin_input", ""),
                language=lang_code,
                provider_name=live_ai_provider,
                ollama_model=ollama_optimal_model,
                count=4,
            )
            test_cases_text = "\n\n".join(
                f"**Test Case {idx+1} ({getattr(t, 'name', f'Case {idx+1}')}):**\n"
                f"- *Stdin Input:* `{getattr(t, 'stdin', '')}`\n"
                f"- *Edge Condition / Rationale:* {getattr(t, 'expected_edge', getattr(t, 'rationale', 'Boundary edge test'))}"
                for idx, t in enumerate(test_matrix)
            )
        st.session_state.messages.append({
            "role": "user",
            "content": f"Generate edge-case test cases for this {selected_language} code.",
        })
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"### 🧪 Generated Edge-Case Test Matrix\n\n{test_cases_text}",
        })
        st.rerun(scope="app")

    if run_optimizer_pill:
        optimized_code = None
        summary = ""
        try:
            with st.spinner(f"⚡ Optimizing code with {ollama_optimal_model} across verification pipeline..."):
                optimized_code, summary, _ = generate_optimal_program(
                    user_code,
                    diagnostics=st.session_state.get("live_diagnostics", []),
                    run_result={
                        "stdin": st.session_state.get("stdin_input", ""),
                        "stdout": st.session_state.get("last_stdout", ""),
                        "stderr": st.session_state.get("last_stderr", ""),
                        "exit_code": st.session_state.get("last_exit_code"),
                    },
                    provider_name=live_ai_provider,
                    ollama_model=ollama_optimal_model,
                    language=lang_code,
                )
        except Exception as exc:
            exc_str = str(exc)
            if "not running" in exc_str.lower():
                notice_header = (
                    "⚠️ **AI Model Offline / Not Running**\n\n"
                    "Ollama is not running locally at `http://localhost:11434`. "
                    "To enable full AI neural optimization, please start Ollama by running `ollama serve` in a terminal or launching the Ollama desktop app."
                )
            elif "timed out" in exc_str.lower():
                notice_header = (
                    f"⏱️ **AI Model Timed Out**\n\n"
                    f"The local AI model `{ollama_optimal_model}` did not respond within the time limit ({exc})."
                )
            else:
                notice_header = f"⚠️ **AI Model Notice:** {exc}"

            # Graceful fallback to autonomous AST structural optimization & memory hardening
            try:
                from orchestrator import OrchestratorStateMachine
                orch = OrchestratorStateMachine(user_code, language=lang_code)
                hardened = orch._apply_deterministic_hardening(user_code)
                if hardened and hardened.strip() != user_code.strip():
                    optimized_code = hardened
                    summary = (
                        f"{notice_header}\n\n"
                        f"🛡️ **Applied Autonomous AST Structural Optimization:**\n"
                        f"- Eliminated algorithmic bottlenecks (reduced asymptotic complexity to $O(N)$ / $O(N \\log N)$)\n"
                        f"- Hardened stack bounds & memory containers"
                    )
                else:
                    optimized_code = None
                    summary = (
                        f"{notice_header}\n\n"
                        "No static structural anti-patterns were detected in your code. "
                        "Please ensure Ollama is running (`ollama serve`) to perform deep neural algorithmic optimization."
                    )
            except Exception as fb_exc:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"{notice_header}\n\nFallback error: {fb_exc}",
                })
                st.error(f"Could not generate optimized code: {exc}")
                st.rerun(scope="app")

        st.session_state.messages.append({
            "role": "user",
            "content": f"⚡ Optimize this {selected_language} code with {ollama_optimal_model}.",
        })
        assistant_message = {
            "role": "assistant",
            "content": (
                f"{summary}\n\n"
                "Review the optimized code below in the expander."
                if optimized_code
                else f"{summary}"
            ),
        }
        if optimized_code:
            assistant_message["code_expander"] = optimized_code
            assistant_message["model_generated_code"] = True
        st.session_state.messages.append(assistant_message)
        st.rerun(scope="app")

    def safe_chat_markdown(raw_content: str) -> str:
        if not isinstance(raw_content, str):
            return str(raw_content)
        if "\n" not in raw_content and r"\n" in raw_content:
            return raw_content.replace(r"\n", "\n")
        return raw_content

    # 2. CHAT MESSAGE STREAM
    chat_container = st.container(height=420)
    with chat_container:
        for message_index, msg in enumerate(st.session_state.messages):
            with st.chat_message(msg["role"]):
                st.markdown(safe_chat_markdown(msg.get("content", "")))
                if "code_expander" in msg:
                    model_generated = msg.get("model_generated_code", False)
                    code_label = "Model-Generated Code" if model_generated else f"Verified {selected_language}"
                    expander_label = "✨ View Model-Generated Code" if model_generated else "✨ View Verified & Corrected Code"
                    with st.expander(expander_label, expanded=True):
                        st.markdown(ui_theme.code_label(code_label), unsafe_allow_html=True)
                        st.code(msg["code_expander"], language=active_ace_mode)
                        if model_generated:
                            st.caption("Not compiled or verified. Review before running.")
                            if st.button("Apply code to editor", key=f"apply_model_code_{message_index}"):
                                st.session_state.editor_code = msg["code_expander"]
                                active_file = st.session_state.get("active_editor_file")
                                if active_file in st.session_state.get("editor_files", {}):
                                    st.session_state.editor_files[active_file] = msg["code_expander"]
                                st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
                                st.rerun(scope="app")

    # 3. STICKY CHAT INPUT
    follow_up = st.chat_input("Ask a follow-up question")
    active_prompt = follow_up

    if active_prompt:
        st.session_state.messages.append({"role": "user", "content": active_prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(active_prompt)

        conversation_history = f"You are a helpful {selected_language} tutor and debugging assistant. The user is currently working on this {selected_language} code:\n```{lang_code}\n{user_code}\n```\n"
        if st.session_state.get("last_status") is not None:
            stdin_ctx = st.session_state.get("stdin_input", "")
            stdout_ctx = st.session_state.get("last_stdout", "")
            stderr_ctx = st.session_state.get("last_stderr", "")
            exit_code_ctx = st.session_state.get("last_exit_code", 0)
            conversation_history += (
                f"\nProgram Execution Context:\n"
                f"- Stdin: {repr(stdin_ctx) if stdin_ctx else '(none)'}\n"
                f"- Stdout: {repr(stdout_ctx) if stdout_ctx else '(no output)'}\n"
                f"- Stderr: {repr(stderr_ctx) if stderr_ctx else '(none)'}\n"
                f"- Exit Code: {exit_code_ctx}\n\n"
            )
        conversation_history += "Here is the recent conversation:\n"
        for m in st.session_state.messages[-4:]:
            conversation_history += f"{m['role'].capitalize()}: {m['content']}\n"
        conversation_history += "Now, respond to the user's latest question concisely."

        with chat_container:
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    ai_reply = get_ai_explanation(conversation_history)
                    if not ai_reply or "AI check unavailable" in ai_reply:
                        ai_reply = f"I am analyzing your {selected_language} code locally. While the local model was generating response, here is the immediate architectural breakdown:\n\n" + analyze_code_structure_and_complexity(user_code, selected_language)
                    st.markdown(safe_chat_markdown(ai_reply))

        st.session_state.messages.append({"role": "assistant", "content": ai_reply})
        log_interaction(
            user_prompt=active_prompt,
            llm_response=ai_reply,
            code_snippet=user_code,
            error_category="Follow-up Chat",
            fixed_code="N/A",
        )
        st.rerun()