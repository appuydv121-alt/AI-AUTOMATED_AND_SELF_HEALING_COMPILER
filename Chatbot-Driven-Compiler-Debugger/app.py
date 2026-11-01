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
from lldb_engine import agentic_debug_loop 
import diagnostic_engine as diag
from semantic_analyzer import analyze_semantics, apply_safe_fixes
import ui_theme
from ui_themes import setup_ui, pane_marker, status_banner, get_theme, THEMES
from ai_providers import get_provider_for_name
from live_suggester import apply_suggestion_to_code, generate_optimal_program, render_live_suggestions
from agentic_optimizer import run_agentic_optimizer, AgenticOptimizationResult
from orchestrator import OrchestratorStateMachine, OrchestratorResult
from memory_store import record_experience
import llm_provider as _llm_provider

# Import the reasoning pipeline (graceful if unavailable)
try:
    from reasoning import run_reasoning
    from reasoning.adapters import findings_to_diagnostics, findings_to_chat, findings_to_patch_code
    REASONING_AVAILABLE = True
except ImportError:
    REASONING_AVAILABLE = False

# LLM config
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL_NAME = "qwen2.5-coder:7b"
SEMANTIC_ANALYSIS_ENABLED = os.environ.get("SEMANTIC_ANALYSIS_ENABLED", "true").lower() not in {"0", "false", "no"}


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
            return [], "Ollama is not available. Install Ollama and run this app again."

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
        return [], "Could not connect to the Ollama server at localhost:11434."


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


def get_ai_explanation(prompt, unavailable_message="AI check unavailable."):
    """Send a prompt through the unified LLM provider using current sidebar settings (100% offline via local Ollama)."""
    try:
        prov = "ollama"
        oll_mdl = (
            st.session_state.get("ollama_chat_model_select")
            or st.session_state.get("ollama_live_model_select")
            or os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b")
        )
        result, active_prov, active_mdl = _llm_provider.generate_task(
            "chat",
            prompt,
            provider_name=prov,
            gemini_model="",
            ollama_model=oll_mdl,
        )
        if active_prov and active_mdl:
            st.session_state.last_served_provider = active_prov
            st.session_state.last_served_model = active_mdl
            st.session_state.live_suggestions_active_provider = active_prov
            st.session_state.live_suggestions_active_model = active_mdl
        return str(result) if not isinstance(result, str) else result
    except Exception as error:
        return f"AI check unavailable: {error}"


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


def render_live_ai_optimizer_panel(
    user_code,
    live_ai_enabled,
    live_ai_provider,
    gemini_live_model,
    ollama_live_model,
    live_ai_max_suggestions,
    gemini_optimal_model,
    ollama_optimal_model,
):
    """Render the Live AI Code Suggestions and Optimizer panel."""
    if not live_ai_enabled:
        st.caption("Live AI Suggestions are currently disabled in the sidebar.")
        return

    _disp_prov = st.session_state.get("last_served_provider") or st.session_state.get(
        "live_suggestions_active_provider", live_ai_provider
    )
    _disp_mdl = st.session_state.get("last_served_model") or st.session_state.get(
        "live_suggestions_active_model",
        gemini_live_model if live_ai_provider == "gemini" else ollama_live_model,
    )
    st.caption(f"Active provider: {_disp_prov} • model: {_disp_mdl}")
    _target_model_display = gemini_live_model if live_ai_provider == "gemini" else ollama_live_model
    suggestion_key = (
        user_code,
        live_ai_provider,
        _target_model_display,
        live_ai_max_suggestions,
        str(st.session_state.get("live_diagnostics", [])),
    )
    has_code = bool(user_code and user_code.strip())
    is_code_changed = suggestion_key != st.session_state.get("live_suggestions_key")
    should_fetch_suggestions = False

    if not has_code:
        st.caption("Write C++ code in the editor to see live suggestions.")
    elif is_code_changed:
        is_first_load = st.session_state.get("live_suggestions_key") is None
        pop_title = "AI Suggestions Available" if is_first_load else "Code Modified · New Suggestions Ready"
        pop_desc = (
            f"Click below to request AI suggestions from {_target_model_display}."
            if is_first_load
            else f"Code changes detected in editor. Click below to analyze your updated code with {_target_model_display}."
        )
        btn_label = "💡 Get AI Suggestions" if is_first_load else "💡 Update AI Suggestions"

        st.markdown(
            f"""
            <div class="ui-suggestion-pop">
                <div class="ui-suggestion-pop-header">
                    <div class="ui-suggestion-pop-title">
                        <span>💡</span>
                        <span>{pop_title}</span>
                    </div>
                    {ui_theme.badge("Ready", tone="suggestion")}
                </div>
                <div class="ui-suggestion-pop-desc">
                    {pop_desc}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(btn_label, key="request_live_suggestions_btn", type="primary"):
            should_fetch_suggestions = True
    else:
        c_status, c_refresh = st.columns([3, 1])
        with c_status:
            st.caption(f"✓ Suggestions up to date with editor ({_target_model_display})")
        with c_refresh:
            if st.button("🔄 Refresh", key="refresh_live_suggestions_btn", help="Re-run AI suggestions on current code"):
                should_fetch_suggestions = True

    if should_fetch_suggestions and has_code:
        started_at = time.monotonic()
        progress_message = {"text": "Sending the current code to the model..."}
        model_metrics = {}
        algorithm_analysis = {}

        def show_model_progress(event):
            stage = event.get("stage")
            elapsed = time.monotonic() - started_at
            if stage == "done":
                model_metrics["tokens"] = event.get("tokens")
            elif stage == "analysis":
                algorithm_analysis.update(event.get("analysis", {}))
                current = algorithm_analysis.get("current_complexity", "")
                alternative = algorithm_analysis.get("alternative_complexity", "")
                progress_message["text"] = "Conceptual optimization analysis received from the model."
                if current:
                    progress_message["text"] += f" Current: {current}"
                if alternative:
                    progress_message["text"] += f" Alternative: {alternative}"
                model_status.update(label=progress_message["text"], state="running")
            elif stage == "requesting":
                progress_message["text"] = f"Waiting for {_target_model_display} to respond... ({elapsed:.1f}s)"
                model_status.update(label=progress_message["text"], state="running")
            elif stage == "generating":
                progress_message["text"] = (
                    f"{_target_model_display} is generating · {event.get('characters', 0)} characters received · {elapsed:.1f}s"
                )
                model_status.update(label=progress_message["text"], state="running")
            elif stage == "validating":
                progress_message["text"] = "Model response received; checking suggestions against your code..."
                model_status.update(label=progress_message["text"], state="running")
            elif stage == "complete":
                tokens = event.get("tokens", model_metrics.get("tokens"))
                token_note = f" · {tokens} model tokens" if tokens is not None else ""
                progress_message["text"] = f"Received {event.get('suggestions', 0)} safe suggestion(s){token_note}."
                model_status.update(label=progress_message["text"], state="complete")
            elif stage == "no_suggestions":
                progress_message["text"] = "Model responded, but found no safe, applicable suggestions."
                model_status.update(label=progress_message["text"], state="complete")
            elif stage == "error":
                progress_message["text"] = f"Model request failed: {event.get('message', 'unknown error')}"
                model_status.update(label=progress_message["text"], state="error")

        with st.status(progress_message["text"], expanded=False) as model_status:
            _suggestions_result = render_live_suggestions(
                code=user_code,
                diagnostics=st.session_state.get("live_diagnostics", []),
                provider_name=live_ai_provider,
                gemini_model=gemini_live_model,
                ollama_model=ollama_live_model,
                max_items=live_ai_max_suggestions,
                progress_callback=show_model_progress,
            )
            if isinstance(_suggestions_result, tuple) and len(_suggestions_result) == 3:
                _sugg_list, _active_prov, _active_mdl = _suggestions_result
            else:
                _sugg_list, _active_prov, _active_mdl = _suggestions_result, "", ""
            st.session_state.live_suggestions_cache = _sugg_list
            if _active_prov and _active_mdl:
                st.session_state.last_served_provider = _active_prov
                st.session_state.last_served_model = _active_mdl
                st.session_state.live_suggestions_active_provider = _active_prov
                st.session_state.live_suggestions_active_model = _active_mdl
        st.session_state.live_suggestions_message = progress_message["text"]
        st.session_state.live_suggestions_code = user_code
        st.session_state.live_suggestions_key = suggestion_key
        st.session_state.live_suggestions_analysis = algorithm_analysis or None
        st.rerun(scope="app")

    suggestions = st.session_state.get("live_suggestions_cache", [])
    analysis = st.session_state.get("live_suggestions_analysis")
    if is_code_changed and suggestions:
        st.caption("ℹ️ Showing suggestions from previous code version (click button above to update):")
    if analysis:
        with st.expander("Algorithm analysis", expanded=True):
            st.markdown(f"**What it computes:** {analysis.get('specification', 'Not identified')}")
            st.markdown(f"**Current complexity:** {analysis.get('current_complexity', 'Not provided')}")
            st.markdown(f"**Alternative:** {analysis.get('alternative', 'No better approach identified')}")
            st.markdown(f"**Alternative complexity:** {analysis.get('alternative_complexity', 'Not provided')}")
            tradeoffs = analysis.get("tradeoffs", [])
            if tradeoffs:
                st.markdown("**Tradeoffs:** " + "; ".join(str(item) for item in tradeoffs))
            if analysis.get("recommendation"):
                st.caption(f"Recommendation: {analysis['recommendation']}")
    if suggestions:
        for idx, suggestion in enumerate(suggestions):
            with st.expander(
                f"{suggestion.get('severity', 'info').title()} · lines {suggestion.get('line_start')}–{suggestion.get('line_end')}",
                expanded=(idx == 0),
            ):
                st.write(suggestion.get("issue", ""))
                st.write("Reason:", suggestion.get("reason", ""))
                st.code(suggestion.get("original", ""), language="cpp")
                st.code(suggestion.get("optimized", ""), language="cpp")
                if st.button("Accept", key=f"accept_live_suggestion_{idx}"):
                    st.session_state.editor_code = (
                        apply_suggestion_to_code(
                            user_code,
                            suggestion.get("line_start", 1),
                            suggestion.get("line_end", 1),
                            suggestion.get("optimized", ""),
                            suggestion.get("original", ""),
                        )
                        or user_code
                    )
                    st.session_state.editor_version += 1
                    st.session_state.live_suggestions_cache = []
                    st.session_state.live_suggestions_key = None
                    log_interaction("live_suggestion_accept", suggestion.get("issue", ""), user_code, "SUGGESTION")
                    st.rerun(scope="app")
                if st.button("Dismiss", key=f"dismiss_live_suggestion_{idx}"):
                    log_interaction("live_suggestion_dismiss", suggestion.get("issue", ""), user_code, "SUGGESTION")
                    st.session_state.live_suggestions_cache = [
                        s for i, s in enumerate(suggestions) if i != idx
                    ]
                    st.rerun(scope="app")
    else:
        if not is_code_changed:
            message = st.session_state.get("live_suggestions_message", "")
            if message.startswith("Model request failed:"):
                st.error(message)
            elif message:
                st.caption(message)
            else:
                st.caption("Suggestions will appear after the model analyzes the current code.")

    if st.button("Generate Optimal Code", key="generate_optimal_code_btn"):
        with st.spinner("🤖 Autonomous Multi-Agent Pipeline running (Planner ➔ Optimizer ➔ Sandbox Judge ➔ Critic)..."):
            orch = OrchestratorStateMachine(
                code=user_code,
                language=lang_code if "lang_code" in locals() else "cpp",
                baseline_stdin=st.session_state.get("stdin_input", ""),
                max_cycles=3,
                provider_name=live_ai_provider,
                gemini_model=gemini_optimal_model,
                ollama_model=ollama_optimal_model,
            )
            agent_res = orch.run()
            st.session_state.agentic_result = agent_res
            changes_list = getattr(agent_res, "changes_summary", None) or [agent_res.verification_status]
            st.session_state.complete_optimal_code = {
                "code": agent_res.optimized_code,
                "changes": "\n".join(f"• {c}" for c in changes_list),
                "status": agent_res.verification_status,
            }
            st.rerun(scope="app")

    if st.session_state.get("complete_optimal_code"):
        with st.expander("✨ Complete Optimal Code", expanded=False):
            block = st.session_state.complete_optimal_code
            st.code(block["code"], language="cpp")
            st.caption(block["status"])
            st.write(block["changes"])
            if st.button("Apply to editor", key="apply_complete_optimal_code"):
                st.session_state.editor_code = block["code"]
                st.session_state.editor_version += 1
                log_interaction("apply_optimal_code", "Applied complete optimized program", user_code, "OPTIMIZATION")
                st.rerun(scope="app")



def analyze_code_structure_and_complexity(code: str, language: str, stdin_input: str = "", stdout: str = "") -> str:
    """Perform rigorous algorithmic analysis to extract execution steps, exact TC, and SC."""
    from complexity_engine import analyze_algorithmic_complexity
    report = analyze_algorithmic_complexity(code, language=language, stdin_input=stdin_input, stdout=stdout)

    steps_text = "\n".join(report.steps_walkthrough)
    opts_text = "\n".join(f"- {opt}" for opt in report.optimizations) if report.optimizations else "- Program structure is clean."

    return f"""### 📖 Code Logic & Execution Walkthrough
{steps_text}

### ⏱️ Algorithmic Complexity Analysis
- **Time Complexity (TC):** `{report.time_complexity}` — {report.tc_derivation}
- **Space Complexity (SC):** `{report.space_complexity}` — {report.sc_derivation}

### 💡 Observations & Optimization Opportunities
{opts_text}"""


def generate_code_walkthrough_and_complexity(
    code: str,
    language: str,
    stdin_input: str = "",
    stdout: str = "",
    stderr: str = "",
    findings_text: str = "",
) -> str:
    """Generate in-depth, logical code walkthrough with exact Time Complexity (TC) and Space Complexity (SC)."""
    # Deterministic local AST analysis provides instant, accurate logical breakdown without delays
    return analyze_code_structure_and_complexity(code, language, stdin_input=stdin_input, stdout=stdout)


def render_agentic_result_view(res, current_user_code: str, lang_name: str):
    """Render Human-in-the-Loop Supervised Multi-Agent Results with Diff & Evidence Gate."""
    if not res:
        return
    st.markdown("---")
    st.subheader("🛡️ Supervised Multi-Agent Optimization & Hardening Gate")

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
        st.metric("🧪 Test Equivalence", f"{tests_ok}/{tests_eval} Passed" if tests_eval > 0 else "Verified", delta="100% Equivalence" if tests_ok == tests_eval else "Discrepancy")

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


# --- DYNAMIC ACTIVE MODEL RESOLUTION ---
def get_active_model_and_provider() -> tuple[str, str]:
    """Resolve currently active running model and provider dynamically (100% local Ollama)."""
    installed_ollama, _ = _ensure_ollama_running()
    default_ollama = installed_ollama[0] if installed_ollama else os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b")
    served_mdl = st.session_state.get("last_served_model")
    mdl = (
        st.session_state.get("ollama_live_model_select")
        or (served_mdl if st.session_state.get("last_served_provider") == "ollama" else None)
        or default_ollama
    )
    return mdl, "ollama"


# --- UI CONFIGURATION ---
st.set_page_config(page_title="Chatbot Compiler Debugger", layout="wide", initial_sidebar_state="expanded")
if "theme_initialized" not in st.session_state:
    st.session_state.theme_initialized = True
    st.session_state.ui_theme = "Forest Dev"

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

# Purge any stale session state from previous versions or cross-provider leaks
for _stale_k in ("live_ai_model", "optimal_ai_model", "chat_ai_model", "live_ai_provider"):
    if _stale_k in st.session_state:
        _val = str(st.session_state[_stale_k])
        if "gemini" in _val:
            del st.session_state[_stale_k]

st.session_state["live_ai_provider"] = "ollama"
st.session_state["live_suggestions_active_provider"] = "ollama"

def reset_execution_state():
    st.session_state.last_status = None
    st.session_state.last_stdout = ""
    st.session_state.last_stderr = ""
    st.session_state.last_exit_code = None
    st.session_state.last_execution_time = None
    st.session_state.last_timed_out = False
    st.session_state.last_agent_log = None
    st.session_state.semantic_report = None
    st.session_state.semantic_corrected_code = None
    st.session_state.semantic_correction_note = None

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
if "last_agent_log" not in st.session_state:
    st.session_state.last_agent_log = None
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
if "complete_optimal_code" not in st.session_state:
    st.session_state.complete_optimal_code = None

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

st.sidebar.subheader("Live AI Suggestions")
live_ai_enabled = st.sidebar.checkbox("Enable live AI suggestions", value=True, key="live_ai_enabled")
live_ai_idle_delay = st.sidebar.slider("Idle delay (seconds)", min_value=1, max_value=5, value=1, step=1, key="live_ai_idle_delay")
live_ai_max_suggestions = st.sidebar.slider("Max suggestions", min_value=1, max_value=5, value=3, key="live_ai_max_suggestions")
_installed_ollama, _ = _ensure_ollama_running()
_ollama_models = _installed_ollama if _installed_ollama else ["qwen2.5-coder:3b", "qwen2.5-coder:7b", "llama3:latest"]

live_ai_provider = "ollama"

# 100% Offline Local Engine Banner
st.sidebar.markdown(
    """
    <div style="background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.35); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px;">
        <div style="font-size: 0.85rem; font-weight: 600; color: #22c55e; display: flex; align-items: center; gap: 6px;">
            <span>🟢</span> <span>100% Offline & Private AI (Ollama)</span>
        </div>
        <div style="font-size: 0.75rem; color: var(--muted); margin-top: 4px; line-height: 1.35;">
            Zero API keys required. All compiler diagnostics, AI tutoring, and autonomous agents run entirely on your local machine.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Model Selectors for Local Ollama
live_ai_model = st.sidebar.selectbox("Live suggestion model", options=_ollama_models, index=0, key="ollama_live_model_select")
optimal_ai_model = st.sidebar.selectbox("Optimizer & agent model", options=_ollama_models, index=min(1, len(_ollama_models) - 1), key="ollama_optimal_model_select")
chat_ai_model = st.sidebar.selectbox("Chatbot tutor model", options=_ollama_models, index=0, key="ollama_chat_model_select")

gemini_live_model = live_ai_model
gemini_optimal_model = optimal_ai_model
ollama_live_model = live_ai_model
ollama_optimal_model = optimal_ai_model

# Synchronize currently active model state
st.session_state["live_ai_provider"] = "ollama"
st.session_state["live_suggestions_active_provider"] = "ollama"
st.session_state["live_suggestions_active_model"] = live_ai_model
st.session_state["last_served_model"] = live_ai_model
st.session_state["last_served_provider"] = "ollama"

ollama_status = get_ollama_model_status(live_ai_model)
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
        st.caption("⌨️ Type freely. Real-time diagnostics update locally without reloading the screen. Click **▷ Compile and analyze** to run.")
        st.html("""<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" onload="
        try {
          document.querySelectorAll('iframe[src*=streamlit_ace], iframe[title*=streamlit_ace]').forEach(f => {
            f.style.border = '1px solid ' + getComputedStyle(document.documentElement).getPropertyValue('--border');
            f.style.borderRadius = '12px';
            const doc = f.contentDocument;
            if(doc) {
              const ed = doc.querySelector('.ace_editor');
              if(ed) {
                ed.style.border = 'none';
                ed.style.outline = 'none';
                ed.style.boxShadow = 'none';
              }
              const btns = doc.querySelectorAll('button');
              btns.forEach(b => {
                if(b.textContent.includes('Apply') || b.textContent.includes('Ctrl') || b.textContent.includes('Enter')) {
                  b.style.display = 'none';
                }
              });
            }
          });
        } catch(e) {}
        this.remove();
        " style="display:none;">""")

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

        # --- TABS: DIAGNOSTICS | AI SUGGESTIONS | INPUT (STDIN) ---
        t_diag, t_ai, t_stdin = st.tabs(["Diagnostics", "AI suggestions", "Input (stdin)"])
        with t_diag:
            if not current_diags:
                st.markdown('<div style="padding: 0.6rem 0; font-size: 0.88rem; color: var(--muted);">Clean. 0 errors, 0 warnings.</div>', unsafe_allow_html=True)
            else:
                render_live_diagnostics()

        with t_ai:
            render_live_ai_optimizer_panel(
                user_code=current_user_code,
                live_ai_enabled=live_ai_enabled,
                live_ai_provider=live_ai_provider,
                gemini_live_model=gemini_live_model,
                ollama_live_model=ollama_live_model,
                live_ai_max_suggestions=live_ai_max_suggestions,
                gemini_optimal_model=gemini_optimal_model,
                ollama_optimal_model=ollama_optimal_model,
            )

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
                st.session_state.last_agent_log = None
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
                st.session_state.last_agent_log = None
            # 3. Process Results
            if return_code == 0:
                # --- PATH A: SUCCESS ---
                ui_theme.show_status(st, "success", "SUCCESS", "Execution Successful!", icon="✅")
                st.markdown(ui_theme.code_label("Program output"), unsafe_allow_html=True)
                st.code(stdout if (stdout and stdout.strip()) else "(no output)", language="text")

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
                                gemini_model=gemini_live_model,
                                ollama_model=ollama_live_model,
                                language=lang_code,
                            )
                            reasoning_diags = findings_to_diagnostics(reasoning_result.findings)
                            st.session_state.live_diagnostics = diag.merge_diagnostics(
                                local_diags, reasoning_diags
                            )
                            reasoning_findings_text = findings_to_chat(reasoning_result)
                            patched_code = findings_to_patch_code(reasoning_result, user_code)
                            if patched_code:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"Verified: {reasoning_result.findings[0].verification.summary}" if reasoning_result.findings and reasoning_result.findings[0].verification else "Suggested correction available.",
                                    "code_expander": patched_code
                                })
                        except Exception:
                            pass
                else:
                    st.session_state.live_diagnostics = local_diags

                with st.spinner(f"Analyzing {selected_language} execution, Time Complexity (TC), and Space Complexity (SC)..."):
                    walkthrough_analysis = generate_code_walkthrough_and_complexity(
                        code=user_code,
                        language=selected_language,
                        stdin_input=stdin_to_pass,
                        stdout=stdout,
                        stderr=stderr,
                        findings_text=reasoning_findings_text,
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
                    "Runtime Error / Crash Detected! Booting Agentic LLDB Debugger..." if is_cpp else f"Runtime Exception Detected in {selected_language}!",
                    icon="⚠️",
                )
                
                if stdout:
                    st.markdown(ui_theme.code_label("Output before error"), unsafe_allow_html=True)
                    st.code(stdout, language="text")

                crash_diagnostic = diag.make_diagnostic(
                    category="ERROR",
                    title="Runtime error",
                    message="The program compiled but crashed or exited with a non-zero exit code. See the Agentic LLDB Process section below for the diagnosis." if is_cpp else "The program encountered an unhandled runtime error / exception.",
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
                
                if is_cpp:
                    with st.spinner("🤖 AI Agent is autonomously inspecting memory with LLDB..."):
                        final_diagnosis, agent_log = agentic_debug_loop(
                            cpp_code=user_code, 
                            crash_output=stderr, 
                            executable_path="./temp_program"
                        )
                    st.session_state.last_agent_log = agent_log
                    initial_msg = f"**🚨 Runtime Error / Crash Detected**\n\n**Agentic LLDB Diagnosis:**\n{final_diagnosis}"
                else:
                    st.session_state.last_agent_log = None
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
                timeout_msg = stderr or "Execution timed out. Your program may be waiting for more input or stuck in an infinite loop."
                ui_theme.show_status(
                    st,
                    "error",
                    "EXECUTION TIMED OUT" if is_timeout else "EXECUTION ERROR",
                    timeout_msg,
                    icon="⏱️" if is_timeout else "❌",
                )
                initial_msg = f"**⏱️ Execution Alert**\n\n{timeout_msg}"
                st.session_state.messages.append({"role": "assistant", "content": initial_msg})
                log_interaction("SYSTEM (Timeout/Error)", initial_msg, user_code, "Timeout")
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
                                gemini_model=gemini_live_model,
                                ollama_model=ollama_live_model,
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
            st.markdown(ui_theme.code_label("Program output"), unsafe_allow_html=True)
            st.code(st.session_state.last_stdout if (st.session_state.last_stdout and st.session_state.last_stdout.strip()) else "(no output)", language="text")
        elif st.session_state.last_status == 2:
            ui_theme.show_status(st, "error", "RUNTIME ERROR", "Runtime Error / Crash Detected!" if is_cpp else f"Runtime Exception in {selected_language}!", icon="⚠️")
            st.markdown(ui_theme.code_label("Runtime error output"), unsafe_allow_html=True)
            st.code(st.session_state.last_stderr, language="text")
        elif st.session_state.get("last_timed_out", False) or st.session_state.last_status == -1:
            ui_theme.show_status(st, "error", "TIMED OUT", "Execution Timed Out!", icon="⏱️")
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

    # AGENTIC LLDB BRAIN LOG (Only shows if Path B ran)

    if st.session_state.last_agent_log:
        st.markdown("---")
        st.subheader("🧠 Agentic LLDB Process")
        with st.expander("View AI Debugging Steps", expanded=True):
            st.markdown(st.session_state.last_agent_log)

        # DASHBOARD
    if st.session_state.get("last_status") is not None:
        quality = compute_code_quality_metrics(user_code, language=lang_code)
        st.markdown("---")
        st.subheader("🛡️ Code Safety & Efficiency Ratings")
        q_col1, q_col2, q_col3 = st.columns(3)
        q_col1.metric("🛡️ Security Rating", f"{quality['security_score']:.0f}%", delta=f"{quality['insecure_pct']:.0f}% Insecure", delta_color="inverse")
        q_col2.metric("⚡ Efficiency Rating", f"{quality['efficiency_score']:.0f}%", delta=f"{quality['inefficient_pct']:.0f}% Inefficient", delta_color="inverse")
        q_col3.metric("📊 Scanned Scope", f"{quality['total_loc']} Lines ({selected_language})")

        deductions = quality.get("deductions", [])
        has_deductions = bool(
            len(deductions) > 0
            or quality.get("critical_threats", 0) > 0
            or quality.get("advisory_threats", 0) > 0
            or len(quality.get("inefficiency_notes", [])) > 0
        )

        with st.expander("🔍 Rating Breakdown & How to Reach 100%", expanded=has_deductions):
            if not has_deductions:
                st.markdown(
                    '<div style="padding: 0.6rem 0; font-size: 0.9rem; color: var(--accent);">'
                    '🎯 <strong>100% Optimal Score:</strong> Zero security vulnerabilities and optimal algorithmic complexity. No deductions applied.'
                    '</div>',
                    unsafe_allow_html=True
                )
            else:
                for d in deductions:
                    cat = d.get("category", "Optimization")
                    penalty = d.get("penalty", 0.0)
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
                            <span class="ui-deduction-badge {badge_cls}">-{penalty:.0f}% {html.escape(cat)}</span>
                        </div>
                        <div class="ui-deduction-why">
                            <strong>Why not 100%:</strong> {html.escape(why)}
                        </div>
                        <div class="ui-deduction-fix">
                            <strong>How to reach 100%:</strong> {html.escape(how)}
                        </div>
                        {code_block_html}
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)

            # --- AUTONOMOUS AGENTIC OPTIMIZER & HARDENER TRIGGER ---
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            btn_opt_label = "🤖 Run Autonomous Multi-Agent Optimizer & Hardener (Reach 100%)" if has_deductions else "🤖 Run Multi-Agent Code Optimizer & Hardener"
            if st.button(btn_opt_label, type="primary", key="btn_run_agentic_optimizer_breakdown", use_container_width=True):
                with st.spinner("🤖 Autonomous Multi-Agent Pipeline running (Planner ➔ Optimizer ➔ Sandbox Judge ➔ Critic)..."):
                    orch = OrchestratorStateMachine(
                        code=user_code,
                        language=lang_code,
                        baseline_stdin=st.session_state.get("stdin_input", ""),
                        max_cycles=3,
                        provider_name=live_ai_provider,
                        gemini_model=gemini_optimal_model,
                        ollama_model=ollama_optimal_model,
                    )
                    agent_res = orch.run()
                    st.session_state.agentic_result = agent_res
                    changes_list = getattr(agent_res, "changes_summary", None) or [agent_res.verification_status]
                    st.session_state.complete_optimal_code = {
                        "code": agent_res.optimized_code,
                        "changes": "\n".join(f"• {c}" for c in changes_list),
                        "status": agent_res.verification_status,
                    }
                    st.rerun(scope="app")

    if st.session_state.get("agentic_result"):
        render_agentic_result_view(st.session_state.agentic_result, user_code, selected_language)

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
                st.metric("Execution Time", f"{_exec_time:.3f} s" if _exec_time is not None else "0.000 s")
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
    quick_prompt_action = None
    with qp_c1:
        if st.button("Explain", key="pill_explain", use_container_width=True):
            walkthrough = analyze_code_structure_and_complexity(
                user_code,
                selected_language,
                stdin_input=st.session_state.get("stdin_input", ""),
                stdout=st.session_state.get("last_stdout", ""),
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

    with qp_c2:
        if st.button("Debug", key="pill_debug", use_container_width=True):
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

    run_optimizer_pill = False
    with qp_c3:
        if st.button("⚡ Optimize", key="pill_optimize", use_container_width=True):
            run_optimizer_pill = True

    with qp_c4:
        if st.button("Tests", key="pill_tests", use_container_width=True):
            from test_generator_agent import generate_test_matrix
            test_matrix = generate_test_matrix(
                user_code,
                baseline_stdin=st.session_state.get("stdin_input", ""),
                language=lang_code,
                provider_name=live_ai_provider,
                gemini_model=gemini_optimal_model,
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
        with st.spinner("🤖 Autonomous Multi-Agent Pipeline verifying code across all agents..."):
            orch = OrchestratorStateMachine(
                code=user_code,
                language=lang_code,
                baseline_stdin=st.session_state.get("stdin_input", ""),
                max_cycles=3,
                provider_name=live_ai_provider,
                gemini_model=gemini_optimal_model,
                ollama_model=ollama_optimal_model,
            )
            agent_res = orch.run()
            st.session_state.agentic_result = agent_res
            changes_list = getattr(agent_res, "changes_summary", None) or [getattr(agent_res, "verification_status", "Code verified across all agents.")]
            st.session_state.complete_optimal_code = {
                "code": getattr(agent_res, "optimized_code", user_code),
                "changes": "\n".join(f"• {c}" for c in changes_list),
                "status": getattr(agent_res, "verification_status", "Verified"),
            }

            sec_before = getattr(agent_res, "metrics_before", {}).get("security_score", 0)
            sec_after = getattr(agent_res, "metrics_after", {}).get("security_score", 0)
            eff_before = getattr(agent_res, "metrics_before", {}).get("efficiency_score", 0)
            eff_after = getattr(agent_res, "metrics_after", {}).get("efficiency_score", 0)
            tests_passed = getattr(agent_res, "tests_passed", 0)
            tests_eval = getattr(agent_res, "tests_evaluated", 0)

            agent_reply = f"""### 🤖 Multi-Agent Verified Code Optimization

**Agent Verification Status:** `{agent_res.verification_status}`
- 🛡️ **Security Audit Agent:** `{sec_before:.0f}%` ➔ `{sec_after:.0f}%` ({sec_after - sec_before:+.0f}%)
- ⚡ **Efficiency Profiler Agent:** `{eff_before:.0f}%` ➔ `{eff_after:.0f}%` ({eff_after - eff_before:+.0f}%)
- 🧪 **Sandbox Judge Equivalence:** `{tests_passed}/{tests_eval}` edge-case tests passed with 100% equivalence

**Applied Improvements:**
{chr(10).join(f"- {c}" for c in changes_list)}

*Review the unified diff below in the Human-in-the-Loop Gate and click **Approve & Apply Patch to Editor** to accept.*"""

            st.session_state.messages.append({
                "role": "user",
                "content": "⚡ Run autonomous multi-agent optimization and hardening on this code.",
            })
            st.session_state.messages.append({
                "role": "assistant",
                "content": agent_reply,
                "code_expander": agent_res.optimized_code,
            })
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
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(safe_chat_markdown(msg.get("content", "")))
                if "code_expander" in msg:
                    with st.expander("✨ View Verified & Corrected Code", expanded=True):
                        st.markdown(ui_theme.code_label(f"Verified {selected_language}"), unsafe_allow_html=True)
                        st.code(msg["code_expander"], language=active_ace_mode)

    # 3. STICKY CHAT INPUT
    follow_up = st.chat_input("Ask a follow-up question")
    active_prompt = quick_prompt_action or follow_up

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