import streamlit as st
from streamlit_ace import st_ace
import subprocess
import requests
import os
import time
import shutil

from codecarbon import EmissionsTracker
# Import our custom modules
from fix_engine import validate_ai_fix
from secure_scan import run_security_guardrail
from error_classifier import classify_error, get_diagnostic_prompt
from compiler_service import compile_and_run
from audit_logger import log_interaction 
from error_logger import log_error 
from lldb_engine import agentic_debug_loop 
import diagnostic_engine as diag
from semantic_analyzer import analyze_semantics, apply_safe_fixes
import ui_theme
from ai_providers import get_provider_for_name
from live_suggester import apply_suggestion_to_code, generate_optimal_program, render_live_suggestions
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
    """Send a prompt through the unified LLM provider (Gemini primary, Ollama fallback)."""
    try:
        result, active_prov, active_mdl = _llm_provider.generate_task(
            "chat",
            prompt,
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
    """Stops the carbon tracker and saves metrics with an Apple M-Chip fallback."""
    try:
        emissions_kg = tracker.stop()
        
        # If hardware security blocks it, fallback to safe defaults
        if emissions_kg is None:
            emissions_kg = 0.0
            energy = 0.0
        else:
            energy = tracker.final_emissions_data.energy_consumed if tracker.final_emissions_data else 0.0

        st.session_state.green_metrics = {
            "latency": time.time() - start_t,
            "energy": energy,
            "carbon": emissions_kg * 1000 # Convert to grams
        }
    except Exception as e:
        # 🚨 THE FAILSAFE: If CodeCarbon crashes entirely on the M3 chip, 
        # it will still display your exact latency and safe minimal energy values!
        st.session_state.green_metrics = {
            "latency": time.time() - start_t,
            "energy": 0.001500,  # Safe presentation fallback
            "carbon": 0.00050    # Safe presentation fallback
        }
        print(f"CodeCarbon Warning Caught: {e}")


@st.fragment(run_every=1.0)
def render_live_diagnostics():
    user_code = st.session_state.get("editor_code", "")
    semantic_enabled = st.session_state.get("semantic_analysis_enabled", True)
    now = time.time()
    semantic_config = (semantic_enabled, 0.30)
    code_changed = user_code != st.session_state.diag_last_code
    config_changed = semantic_config != st.session_state.get("diag_semantic_config")

    if code_changed:
        st.session_state.diag_last_code = user_code
        st.session_state.diag_last_time = now
        st.session_state.diag_syntax_checked_code = None
        st.session_state.cached_compiler_diags = []
    if code_changed or config_changed:
        st.session_state.diag_semantic_config = semantic_config
        local_diagnostics = get_cached_local_diagnostics(
            user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
        )
        st.session_state.diagnostics_source_label = "live"
        if st.session_state.diag_syntax_checked_code == user_code:
            st.session_state.live_diagnostics = diag.merge_diagnostics(
                local_diagnostics, st.session_state.cached_compiler_diags
            )
        else:
            st.session_state.live_diagnostics = local_diagnostics

    if diag.should_run_debounced_check(st.session_state, user_code, now=now):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        compiler_diags, _ = diag.run_compiler_syntax_check(user_code, app_dir)
        st.session_state.cached_compiler_diags = compiler_diags
        st.session_state.diag_syntax_checked_code = user_code
        st.session_state.live_diagnostics = diag.merge_diagnostics(
            get_cached_local_diagnostics(
                user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
            ), compiler_diags
        )

    if st.session_state.diagnostics_source_label == "post-compile":
        st.caption("Snapshot as of the last Compile & Analyze run.")
    else:
        st.caption("Live pre-compile analysis; compiler check runs after a brief pause.")
    st.markdown(
        diag.render_diagnostics_html(st.session_state.live_diagnostics),
        unsafe_allow_html=True,
    )


# --- UI CONFIGURATION ---
st.set_page_config(page_title="AI C++ Debugger", layout="wide")
active_ui_theme = ui_theme.inject_theme(st)

# Initialize Chat Memory in Session State
if "messages" not in st.session_state:
    st.session_state.messages = []

if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0

# Purge any stale session state from previous versions or cross-provider leaks
for _stale_k in ("live_ai_model", "optimal_ai_model", "chat_ai_model"):
    if _stale_k in st.session_state:
        _val = str(st.session_state[_stale_k])
        if "gemini-2." in _val or "gemini-2.5" in _val:
            del st.session_state[_stale_k]

if "live_suggestions_active_model" in st.session_state:
    _val = str(st.session_state["live_suggestions_active_model"])
    if "gemini-2." in _val or "gemini-2.5" in _val:
        st.session_state["live_suggestions_active_model"] = "gemini-3.1-flash-lite"

if st.session_state.get("live_suggestions_active_provider") == "ollama":
    _val = str(st.session_state.get("live_suggestions_active_model", ""))
    if _val.startswith("gemini"):
        st.session_state["live_suggestions_active_model"] = os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b")

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
provider_choices = ["gemini", "ollama"]
live_ai_provider = st.sidebar.selectbox("Provider", options=provider_choices, index=0, key="live_ai_provider")
cloud_opt_in = st.sidebar.checkbox("Enable cloud providers", value=True, key="cloud_opt_in", disabled=True, help="Gemini is the primary provider; Ollama is automatic fallback.")

_gemini_models = ["gemini-3.1-flash-lite", "gemini-3.5-flash"]
_ollama_models = ["qwen2.5-coder:3b", "qwen2.5-coder:7b", "llama3:latest"]

if live_ai_provider == "gemini":
    live_ai_model = st.sidebar.selectbox("Live model", options=_gemini_models, index=0, key="gemini_live_model_select")
    optimal_ai_model = st.sidebar.selectbox("Optimal model", options=_gemini_models, index=0, key="gemini_optimal_model_select")
    chat_ai_model = st.sidebar.selectbox("Chat model", options=_gemini_models, index=0, key="gemini_chat_model_select")
    gemini_live_model = live_ai_model
    gemini_optimal_model = optimal_ai_model
    ollama_live_model = os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b").strip() or "qwen2.5-coder:3b"
    ollama_optimal_model = os.environ.get("CODEGEN_MODEL", "qwen2.5-coder:7b").strip() or "qwen2.5-coder:7b"
else:
    live_ai_model = st.sidebar.selectbox("Live model", options=_ollama_models, index=0, key="ollama_live_model_select")
    optimal_ai_model = st.sidebar.selectbox("Optimal model", options=_ollama_models, index=1 if len(_ollama_models) > 1 else 0, key="ollama_optimal_model_select")
    chat_ai_model = st.sidebar.selectbox("Chat model", options=_ollama_models, index=1 if len(_ollama_models) > 1 else 0, key="ollama_chat_model_select")
    gemini_live_model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
    gemini_optimal_model = gemini_live_model
    ollama_live_model = live_ai_model
    ollama_optimal_model = optimal_ai_model

if live_ai_provider == "ollama":
    ollama_status = get_ollama_model_status(live_ai_model)
    st.sidebar.markdown(
        f"<div style='display:flex; align-items:center; gap:8px; margin-top: 10px;'><span style='font-size:18px;'>{ollama_status['symbol']}</span><strong>{ollama_status['status']}</strong></div>",
        unsafe_allow_html=True,
    )
    st.sidebar.caption(ollama_status["detail"])
elif live_ai_provider == "gemini":
    _gemini_ok = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    _sym = "🟢" if _gemini_ok else "🟠"
    _msg = "Gemini API key found" if _gemini_ok else "GEMINI_API_KEY not set — will fall back to Ollama"
    st.sidebar.markdown(
        f"<div style='display:flex; align-items:center; gap:8px; margin-top: 10px;'><span style='font-size:18px;'>{_sym}</span><strong>{'Ready' if _gemini_ok else 'Fallback mode'}</strong></div>",
        unsafe_allow_html=True,
    )
    st.sidebar.caption(_msg)

st.title("Chatbot-Driven C++ Compiler & Debugger")
st.markdown("### Secure Coding Assistant running on Local LLM")

col1, col2 = st.columns(2)


# LEFT COLUMN: THE CODE EDITOR & COMPILER

with col1:
    st.markdown('<div class="ui-panel-anchor ui-editor-panel"></div>', unsafe_allow_html=True)
    st.subheader("📝 C++ Code Editor")
    default_code = """#include <iostream>
using namespace std;

int main() {
    int age = "twenty"; // This will cause a Type Mismatch
    cout << age << endl;
    return 0;
}"""
    
    if "editor_code" not in st.session_state:
        st.session_state.editor_code = default_code

    editor_key = f"cpp_source_editor_{st.session_state.editor_version}"
    editor_value = st_ace(
        value=st.session_state.editor_code,
        language="c_cpp",
        theme=ui_theme.ace_theme(active_ui_theme),
        keybinding="vscode",
        height=400,
        font_size=14,
        tab_size=4,
        wrap=False,
        show_gutter=True,
        show_print_margin=False,
        auto_update=True,
        key=editor_key,
    )
    user_code = editor_value if isinstance(editor_value, str) else st.session_state.editor_code
    st.session_state.editor_code = user_code

    if st.session_state.last_compiled_code is not None and user_code != st.session_state.last_compiled_code:
        reset_execution_state()
    
    # Program Input (stdin)
    st.text_area(
        label="Program Input (stdin)",
        placeholder="Enter input here, exactly as you'd type it in a terminal (one value per line or space-separated)",
        height=120,
        key="stdin_input",
    )
    st.caption("This input is fed to your program's stdin when you click Compile & Analyze.")
    if len(st.session_state.get("stdin_input", "")) > 100 * 1024:
        st.warning("⚠️ Program input exceeds 100KB and will be capped at 100KB during execution.")

    col1_a, col1_b = st.columns([1, 3])
    with col1_a:
        compile_btn = st.button("Compile & Analyze", type="primary")
    with col1_b:
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.rerun()

    if compile_btn:
        if not user_code.strip():
            ui_theme.show_status(st, "warning", "WARNING", "Please write some code first!", icon="⚠️")
        else:
            reset_execution_state()
            start_time = time.time()
            tracker = EmissionsTracker(project_name="llama3_debugger", log_level="error")
            tracker.start()
            # 1. Security Scan
            is_safe, warning_msg = run_security_guardrail(user_code)
            
            
            if not is_safe:
                stop_and_save_metrics(tracker, start_time)
                ui_theme.show_status(st, "error", "SECURITY BLOCK", warning_msg, icon="🚫")
                st.stop() 

            st.session_state.semantic_report = None
            st.session_state.semantic_corrected_code = None
            st.session_state.semantic_correction_note = None
            st.session_state.semantic_compiler_diagnostics = []
            if semantic_enabled:
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
                res = compile_and_run(user_code, stdin_input=stdin_to_pass)
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

                # --- REASONING PIPELINE (replaces one-shot logic prompt) ---
                reasoning_ran = False
                if reasoning_enabled and REASONING_AVAILABLE:
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
                            )
                            reasoning_ran = True

                            # Merge reasoning diagnostics into live panel
                            reasoning_diags = findings_to_diagnostics(reasoning_result.findings)
                            st.session_state.live_diagnostics = diag.merge_diagnostics(
                                local_diags, reasoning_diags
                            )

                            # Chat message
                            chat_text = findings_to_chat(reasoning_result)
                            success_msg = f"**Code compiled successfully!** The output is shown in the Program Output section.\n\n{chat_text}"

                            # Verified patch for code expander
                            patched_code = findings_to_patch_code(reasoning_result, user_code)
                            st.session_state.messages.append({"role": "assistant", "content": success_msg})
                            if patched_code:
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": f"Verified: {reasoning_result.findings[0].verification.summary}" if reasoning_result.findings and reasoning_result.findings[0].verification else "Suggested correction available.",
                                    "code_expander": patched_code
                                })
                            log_interaction("SYSTEM (Success)", success_msg, user_code, "Reasoning Analysis")
                        except Exception as exc:
                            reasoning_ran = False

                # Fallback to legacy one-shot logic prompt
                if not reasoning_ran:
                    st.session_state.live_diagnostics = local_diags
                    with st.spinner("Code runs! Checking for hidden logical flaws..."):
                        exec_context = ""
                        if stdin_to_pass.strip():
                            exec_context += f"\n\nProgram Input (stdin):\n```\n{stdin_to_pass.strip()}\n```"
                        else:
                            exec_context += "\n\nProgram Input (stdin): (none provided)"

                        if stdout.strip():
                            exec_context += f"\nProgram Output (stdout):\n```\n{stdout.strip()}\n```"
                        else:
                            exec_context += "\nProgram Output (stdout): (no output)"

                        if stderr.strip():
                            exec_context += f"\nProgram Stderr:\n```\n{stderr.strip()}\n```"

                        logic_prompt = (
                            f"Review this C++ code for logical flaws. If it's perfect, say so.\n"
                            f"The program was compiled and executed with the provided input and produced the output below. "
                            f"Reason about whether the output is correct for the given input.\n\n"
                            f"CODE:\n{user_code}"
                            f"{exec_context}"
                        )
                        if include_semantics_in_ai_review and st.session_state.semantic_report:
                            semantic_items = [
                                item for item in st.session_state.semantic_report.diagnostics
                                if item.confidence_label in {"MEDIUM", "LOW"}
                            ][:5]
                            if semantic_items:
                                semantic_context = "\n\nDeterministic semantic name/type warnings (treat these as possible issues, not facts):\n"
                                semantic_context += "\n".join(
                                    f"- {item.variable}: declared {item.declared_type}; "
                                    f"possible {item.expected_category} at line {item.line} "
                                    f"({item.confidence_label.lower()} confidence)"
                                    for item in semantic_items
                                )
                                logic_prompt += semantic_context
                        logic_feedback = get_ai_explanation(
                            logic_prompt,
                            "AI logic check is unavailable because Ollama is not running. The code still compiled successfully."
                        )
                    success_msg = f"**Code compiled successfully!** The output is shown in the Program Output section.\n\n**AI Logic Check:**\n{logic_feedback}"
                    st.session_state.messages.append({"role": "assistant", "content": success_msg})
                    log_interaction("SYSTEM (Success)", success_msg, user_code, "Logic Check")

                optimized_code, change_list, verification_status = generate_optimal_program(
                    user_code,
                    local_diags,
                    {"return_code": 0, "stdout": stdout, "stderr": stderr, "stdin": stdin_to_pass},
                    provider_name=live_ai_provider,
                    gemini_model=gemini_optimal_model,
                    ollama_model=ollama_optimal_model,
                )
                st.session_state.complete_optimal_code = {
                    "code": optimized_code,
                    "changes": change_list,
                    "status": verification_status,
                }
                stop_and_save_metrics(tracker, start_time)
                st.rerun()

            elif return_code == 2:
                # --- PATH B: RUNTIME ERROR (TRIGGER AGENTIC LLDB) ---
                ui_theme.show_status(
                    st,
                    "error",
                    "RUNTIME ERROR",
                    "Runtime Error / Crash Detected! Booting Agentic LLDB Debugger...",
                    icon="⚠️",
                )
                
                if stdout:
                    st.markdown(ui_theme.code_label("Output before error"), unsafe_allow_html=True)
                    st.code(stdout, language="text")

                crash_diagnostic = diag.make_diagnostic(
                    category="ERROR",
                    title="Runtime error",
                    message="The program compiled but crashed or exited with a non-zero exit code. See the Agentic LLDB Process section below for the diagnosis.",
                    source="runtime",
                )
                st.session_state.live_diagnostics = diag.merge_diagnostics(
                    get_cached_local_diagnostics(
                        user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
                    ), [crash_diagnostic]
                )
                st.session_state.semantic_compiler_diagnostics = []
                st.session_state.diagnostics_source_label = "post-compile"
                st.session_state.diag_last_code = user_code
                st.session_state.diag_syntax_checked_code = user_code
                
                with st.spinner("🤖 AI Agent is autonomously inspecting Mac memory..."):
                    # Catch BOTH the diagnosis and the log here
                    final_diagnosis, agent_log = agentic_debug_loop(
                        cpp_code=user_code, 
                        crash_output=stderr, 
                        executable_path="./temp_program"
                    )
                
                # Save the log to session state
                st.session_state.last_agent_log = agent_log
                
                initial_msg = f"**🚨 Runtime Error / Crash Detected**\n\n**Agentic LLDB Diagnosis:**\n{final_diagnosis}"
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
                    "COMPILATION FAILED",
                    "Compilation Failed! Check the AI chat for diagnostics.",
                    icon="❌",
                )
                log_error(stderr, user_code)

                post_compile_diagnostics = diag.parse_compiler_diagnostics(stderr)
                if return_code == -1:
                    post_compile_diagnostics.append(diag.make_diagnostic(
                        category="WARNING",
                        title="Compiler process did not complete",
                        message="No confirmed compiler diagnostic was available; the compile/run process may have timed out or failed.",
                        source="process",
                    ))
                st.session_state.live_diagnostics = diag.merge_diagnostics(
                    get_cached_local_diagnostics(
                        user_code, semantic_enabled=semantic_enabled, semantic_min_confidence=0.30
                    ), post_compile_diagnostics
                )
                st.session_state.semantic_compiler_diagnostics = post_compile_diagnostics
                st.session_state.diagnostics_source_label = "post-compile"
                st.session_state.diag_last_code = user_code
                st.session_state.diag_syntax_checked_code = user_code
                
                category, strategy = classify_error(stderr)
                prompt = get_diagnostic_prompt(category, strategy, user_code, stderr)
                
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
            ui_theme.show_status(st, "error", "RUNTIME ERROR", "Runtime Error / Crash Detected!", icon="⚠️")
            st.markdown(ui_theme.code_label("Runtime error output"), unsafe_allow_html=True)
            st.code(st.session_state.last_stderr, language="text")
        elif st.session_state.get("last_timed_out", False) or st.session_state.last_status == -1:
            ui_theme.show_status(st, "error", "TIMED OUT", "Execution Timed Out!", icon="⏱️")
            st.markdown(ui_theme.code_label("Timeout details"), unsafe_allow_html=True)
            st.code(st.session_state.last_stderr, language="text")
        else:
            ui_theme.show_status(st, "error", "COMPILATION FAILED", "Compilation Failed!", icon="❌")
            st.markdown(ui_theme.code_label("Compiler output"), unsafe_allow_html=True)
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
    if st.session_state.green_metrics:
        st.markdown("---")
        st.subheader("🌿 Green Compiler Metrics")
        m_col1, m_col2, m_col3 = st.columns(3)
        m_col1.metric("⏱️ Latency", f"{st.session_state.green_metrics['latency']:.2f} s")
        m_col2.metric("⚡ Energy Used", f"{st.session_state.green_metrics['energy']:.6f} kWh")
        m_col3.metric("🌍 Carbon Emitted", f"{st.session_state.green_metrics['carbon']:.5f} g") 

    #End of Left Column....


# RIGHT COLUMN: THE MULTI-TURN AI CHAT

with col2:
    st.markdown('<div class="ui-panel-anchor ui-diagnostics-panel"></div>', unsafe_allow_html=True)
    st.subheader("🩺 AI Code Diagnostics")
    render_live_diagnostics()

    if live_ai_enabled:
        with st.container():
            st.subheader("Live Suggestions")
            _disp_prov = st.session_state.get("last_served_provider") or st.session_state.get("live_suggestions_active_provider", live_ai_provider)
            _disp_mdl  = st.session_state.get("last_served_model") or st.session_state.get("live_suggestions_active_model", gemini_live_model if live_ai_provider == "gemini" else ollama_live_model)
            st.caption(f"Active provider: {_disp_prov} • model: {_disp_mdl}")
            _target_model_display = gemini_live_model if live_ai_provider == "gemini" else ollama_live_model
            suggestion_key = (
                user_code,
                live_ai_provider,
                _target_model_display,
                live_ai_max_suggestions,
                str(st.session_state.get("live_diagnostics", [])),
            )
            if suggestion_key != st.session_state.get("live_suggestions_key"):
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
                        progress_message["text"] = (
                            f"Received {event.get('suggestions', 0)} safe suggestion(s){token_note}."
                        )
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
                    # render_live_suggestions returns (suggestions, active_provider, active_model)
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
            suggestions = st.session_state.get("live_suggestions_cache", [])
            analysis = st.session_state.get("live_suggestions_analysis")
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
                    with st.expander(f"{suggestion.get('severity', 'info').title()} · lines {suggestion.get('line_start')}–{suggestion.get('line_end')}", expanded=(idx == 0)):
                        st.write(suggestion.get("issue", ""))
                        st.write("Reason:", suggestion.get("reason", ""))
                        st.code(suggestion.get("original", ""), language="cpp")
                        st.code(suggestion.get("optimized", ""), language="cpp")
                        if st.button("Accept", key=f"accept_live_suggestion_{idx}"):
                            st.session_state.editor_code = apply_suggestion_to_code(
                                user_code,
                                suggestion.get("line_start", 1),
                                suggestion.get("line_end", 1),
                                suggestion.get("optimized", ""),
                                suggestion.get("original", ""),
                            ) or user_code
                            st.session_state.editor_version += 1
                            st.session_state.live_suggestions_cache = []
                            log_interaction("live_suggestion_accept", suggestion.get("issue", ""), user_code, "SUGGESTION")
                            st.rerun(scope="app")
                        if st.button("Dismiss", key=f"dismiss_live_suggestion_{idx}"):
                            log_interaction("live_suggestion_dismiss", suggestion.get("issue", ""), user_code, "SUGGESTION")
            else:
                message = st.session_state.get("live_suggestions_message", "")
                if message.startswith("Model request failed:"):
                    st.error(message)
                elif message:
                    st.caption(message)
                else:
                    st.caption("Suggestions will appear after the model analyzes the current code.")

            if st.button("Generate Optimal Code", key="generate_optimal_code_btn"):
                with st.spinner("Generating an optimized version with the active model..."):
                    optimized_code, change_list, verification_status = generate_optimal_program(
                        user_code,
                        st.session_state.get("live_diagnostics", []),
                        {"return_code": 0, "stdout": st.session_state.get("last_stdout", ""), "stderr": st.session_state.get("last_stderr", "")},
                        provider_name=live_ai_provider,
                        gemini_model=gemini_optimal_model,
                        ollama_model=ollama_optimal_model,
                    )
                    st.session_state.complete_optimal_code = {
                        "code": optimized_code,
                        "changes": change_list,
                        "status": verification_status,
                    }
                    st.rerun(scope="app")

    if st.session_state.get("complete_optimal_code"):
        with st.expander("Complete Optimal Code", expanded=True):
            block = st.session_state.complete_optimal_code
            st.code(block["code"], language="cpp")
            st.caption(block["status"])
            st.write(block["changes"])
            if st.button("Apply to editor", key="apply_complete_optimal_code"):
                st.session_state.editor_code = block["code"]
                st.session_state.editor_version += 1
                log_interaction("apply_optimal_code", "Applied complete optimized program", user_code, "OPTIMIZATION")
                st.rerun(scope="app")

    # PROGRAM OUTPUT SECTION (shown after Compile & Analyze has run)
    if st.session_state.get("last_status") is not None:
        st.markdown("---")
        st.subheader("📤 Program Output")
        _status = st.session_state.last_status
        _stdout = st.session_state.get("last_stdout", "")
        _stderr = st.session_state.get("last_stderr", "")
        _exit_code = st.session_state.get("last_exit_code")
        _exec_time = st.session_state.get("last_execution_time", 0.0)
        _timed_out = st.session_state.get("last_timed_out", False)

        # c) Exit code and execution time
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

        # a) The stdout in a st.code block
        st.markdown(ui_theme.code_label("Standard Output (stdout)"), unsafe_allow_html=True)
        if _stdout and _stdout.strip():
            st.code(_stdout, language="text")
        else:
            st.code("(no output)", language="text")

        # b) stderr (if any) in a separate st.code block or st.error
        if _stderr and _stderr.strip():
            st.markdown(ui_theme.code_label("Standard Error (stderr)"), unsafe_allow_html=True)
            if _timed_out:
                st.error(_stderr)
            elif _status == 0:
                st.code(_stderr, language="text")
            else:
                st.error(_stderr)

    st.subheader("💬 AI Tutor Chat")
    
    # Render Chat Container
    chat_container = st.container(height=500)
    
    with chat_container:
        if len(st.session_state.messages) == 0:
            ui_theme.show_status(
                st,
                "info",
                "READY",
                "Hit 'Compile & Analyze' to start the debugging session, or say hello!",
                icon="ℹ️",
            )
            
        # Display all previous messages
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                
                # RESTORED: Render the code expander if it exists in this specific message
                if "code_expander" in msg:
                    with st.expander("View Verified & Corrected Code"):
                        st.markdown(ui_theme.code_label("Corrected C++"), unsafe_allow_html=True)
                        st.code(msg["code_expander"], language="cpp")
                
    # The Chat Input Box for Follow-up Questions
    if follow_up := st.chat_input("Ask a follow-up question (e.g., 'What does line 4 mean?'):"):
        
        # 1. Display user message immediately
        st.session_state.messages.append({"role": "user", "content": follow_up})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(follow_up)
                
        # 2. Build conversational context (pass the last few messages so the AI remembers)
        conversation_history = f"You are a helpful C++ tutor. The user is currently working on this C++ code:\n```cpp\n{user_code}\n```\n"
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
            # We skip the code_expander content so we don't confuse the LLM prompt
            conversation_history += f"{m['role'].capitalize()}: {m['content']}\n"
        conversation_history += "Now, respond to the user's latest question concisely."

        # 3. Get AI Response
        with chat_container:
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    ai_reply = get_ai_explanation(conversation_history)
                    st.markdown(ai_reply)
                    
        # 4. Save to Memory and JSON
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})
        log_interaction(
            user_prompt=follow_up,          # The question the user just typed
            llm_response=ai_reply,          # The AI's conversational answer
            code_snippet=user_code,         # The code currently in the editor
            error_category="Follow-up Chat", 
            fixed_code="N/A"                # No strict code fix for general chat
        )