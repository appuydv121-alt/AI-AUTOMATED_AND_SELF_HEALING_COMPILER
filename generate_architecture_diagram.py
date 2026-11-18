import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch
import os
import shutil

def generate_system_architecture():
    # Setup ultra-high resolution figure (28 x 17 inches at 200 dpi -> 5600 x 3400 pixels)
    fig, ax = plt.subplots(figsize=(28, 17), dpi=200)
    fig.patch.set_facecolor('#070B19')
    ax.set_facecolor('#070B19')
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')

    # Color Palette
    c_ui       = '#0EA5E9'  # Sky Blue: Streamlit UI & Client
    c_security = '#F59E0B'  # Amber: Security & Guardrails
    c_compiler = '#10B981'  # Emerald: Sandboxed Execution & g++
    c_agentic  = '#EC4899'  # Rose/Pink: Agentic LLDB Debug Loop
    c_orch     = '#8B5CF6'  # Purple: Multi-Agent Orchestrator
    c_reason   = '#A855F7'  # Violet: Deep Reasoning Engine
    c_llm      = '#06B6D4'  # Cyan: Local Ollama Qwen 7B
    c_green    = '#14B8A6'  # Teal: Green Telemetry & Memory
    c_box_bg   = '#0F172A'  # Slate-900 Box Background

    # Helper: Draw Card
    def draw_card(x, y, w, h, border_color, title, subtitle="", items=[], icon=""):
        # Drop shadow
        shadow = FancyBboxPatch((x + 0.35, y - 0.35), w, h, boxstyle="round,pad=0.2,rounding_size=1.2",
                                facecolor="#020617", edgecolor="none", alpha=0.6, zorder=2)
        ax.add_patch(shadow)

        # Card container
        box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1.2",
                             facecolor=c_box_bg, edgecolor=border_color, linewidth=2.0, zorder=3)
        ax.add_patch(box)

        # Header bar
        header_bar = FancyBboxPatch((x + 0.1, y + h - 3.2), w - 0.2, 3.1, boxstyle="round,pad=0.1,rounding_size=0.8",
                                    facecolor=border_color, edgecolor="none", alpha=0.2, zorder=4)
        ax.add_patch(header_bar)

        # Title text
        display_title = f"{icon}  {title}" if icon else title
        ax.text(x + 1.2, y + h - 1.8, display_title, fontsize=12.5, fontweight='bold',
                color="#FFFFFF", zorder=5, fontfamily='sans-serif')

        curr_y = y + h - 4.4
        if subtitle:
            sub_w = len(subtitle) * 0.44 + 1.8
            badge = FancyBboxPatch((x + 1.2, curr_y - 0.8), sub_w, 1.8, boxstyle="round,pad=0.1,rounding_size=0.4",
                                   facecolor="#1E293B", edgecolor=border_color, linewidth=1.0, zorder=5)
            ax.add_patch(badge)
            ax.text(x + 1.8, curr_y, subtitle, fontsize=8.8, fontweight='bold',
                    color=border_color, zorder=6, fontfamily='sans-serif')
            curr_y -= 2.6

        for item in items:
            if isinstance(item, tuple):
                curr_y -= 0.3
                ax.text(x + 1.2, curr_y, item[0], fontsize=9.2, fontweight='bold',
                        color='#E2E8F0', zorder=5, fontfamily='sans-serif')
                curr_y -= 1.6
                for sub in item[1]:
                    ax.text(x + 2.5, curr_y, sub, fontsize=8.2, color='#94A3B8',
                            zorder=5, fontfamily='monospace' if any(c in sub for c in ['()','{}','-','g++','lldb','.py']) else 'sans-serif')
                    curr_y -= 1.5
            else:
                is_code = any(c in item for c in ['()', '{}', '->', 'CWE-', 'Exit', 'http', 'g++', 'lldb', '.py', '.db', '.csv'])
                color = '#CBD5E1' if item.startswith('•') else '#94A3B8'
                weight = 'bold' if item.startswith('•') and (':' in item or item.endswith(':')) else 'normal'
                indent = 1.2 if item.startswith('•') else 2.4
                ax.text(x + indent, curr_y, item, fontsize=8.4, fontweight=weight,
                        color=color, zorder=5, fontfamily='monospace' if is_code else 'sans-serif')
                curr_y -= 1.55

    # Helper: Draw Connecting Arrow
    def draw_arrow(x1, y1, x2, y2, color, label="", text_pos=0.5, rad=0.0):
        arrow = patches.FancyArrowPatch((x1, y1), (x2, y2),
                                        connectionstyle=f"arc3,rad={rad}",
                                        arrowstyle="-|>,head_length=6,head_width=4",
                                        color=color, linewidth=2.0, zorder=10)
        ax.add_patch(arrow)
        if label:
            mx = x1 + (x2 - x1) * text_pos
            my = y1 + (y2 - y1) * text_pos
            if rad != 0:
                my += rad * 9
            lbl_w = len(label) * 0.40 + 1.2
            lbl_box = FancyBboxPatch((mx - lbl_w/2, my - 0.8), lbl_w, 1.6, boxstyle="round,pad=0.1,rounding_size=0.4",
                                     facecolor="#0B1329", edgecolor=color, linewidth=1.0, zorder=11)
            ax.add_patch(lbl_box)
            ax.text(mx, my, label, fontsize=8.0, fontweight='bold', color=color,
                    ha='center', va='center', zorder=12, fontfamily='sans-serif')

    # ─────────────────────────────────────────────────────────────────────────────
    # TITLE BANNER
    # ─────────────────────────────────────────────────────────────────────────────
    title_box = FancyBboxPatch((2, 93), 96, 5.8, boxstyle="round,pad=0.2,rounding_size=1.0",
                               facecolor="#0F172A", edgecolor="#38BDF8", linewidth=1.5, zorder=3)
    ax.add_patch(title_box)

    ax.text(50, 96.6, "TOACD: CHATBOT-DRIVEN C++ COMPILER & AGENTIC DEBUGGER",
            fontsize=17, fontweight='heavy', color='#F8FAFC', ha='center', va='center', zorder=5, fontfamily='sans-serif')
    ax.text(50, 94.4, "End-to-End System Architecture: Multi-Agent Orchestration • Sandboxed LLDB Debugging • Security Guardrails • Green Telemetry",
            fontsize=10.5, fontweight='bold', color='#38BDF8', ha='center', va='center', zorder=5, fontfamily='sans-serif')

    # ─────────────────────────────────────────────────────────────────────────────
    # COLUMN 1: PRESENTATION & CLIENT LAYER (x: 2 to 24)
    # ─────────────────────────────────────────────────────────────────────────────
    draw_card(2, 53, 22, 38, c_ui, "UI & IDE Studio Layer", "app.py • Streamlit Multi-Pane", [
        ("• Ace Code Editor Workspace", [
            "Real-time syntax highlighting (C++17)",
            "Multi-tab files & safe editing buffer",
            "Auto line-numbering & caret tracking"
        ]),
        ("• Real-Time Diagnostics Panel", [
            "Local diagnostic_engine.py checks",
            "Non-blocking background validation",
            "Dynamic compilation error markers"
        ]),
        ("• Interactive Pedagogical Chat", [
            "Multi-turn conversational context",
            "One-click 'Apply Fix' directly to editor",
            "Explain • Debug • Optimize • Test pills"
        ]),
        ("• Green Profiler Dashboard", [
            "Real-time latency (s) & energy (kWh)",
            "Grams CO2 equivalent tracking",
            "Historical telemetry log viewer"
        ])
    ], icon="[IDE]")

    draw_card(2, 7, 22, 43, c_green, "Observability & Telemetry", "Audit Logs • Emissions • Memory", [
        ("• CodeCarbon Green Profiler", [
            "Real-time CPU/RAM wattage tracker",
            "Logs every run to emissions.csv",
            "Hardware profiling (Ryzen / Apple M)"
        ]),
        ("• Long-Term Experience Memory", [
            "memory_store.py (SQLite DB)",
            "Caches successful debugging fixes",
            "Retrieves past fixes via similarity"
        ]),
        ("• Audit & Interaction Logs", [
            "system_interactions.json logs",
            "security_audit.log incident tracking",
            "Deterministic timestamped run records"
        ]),
        ("• Empirical Profiler Benchmark", [
            "empirical_profiler.py runtime profiler",
            "Per-function execution duration",
            "Stress-testing microbenchmarks"
        ])
    ], icon="[METRICS]")

    # ─────────────────────────────────────────────────────────────────────────────
    # COLUMN 2: SECURITY GUARDRAILS & SANDBOXED COMPILER (x: 26 to 49)
    # ─────────────────────────────────────────────────────────────────────────────
    draw_card(26, 68, 23, 23, c_security, "Pre-Execution Guardrail", "secure_scan.py • Static AST Scan", [
        "• Regex & AST Hazardous Token Inspection:",
        "  - System calls: system(), popen(), fork()",
        "  - Process control: exec*(), ptrace(), kill()",
        "  - File traversal: <filesystem>, chdir(), unlink()",
        "  - Raw networking: socket(), connect(), send()",
        "• Security Policy Gatekeeper:",
        "  - Blocks malicious code prior to compilation",
        "  - Zero untrusted shell execution allowed",
        "  - Emits security alert to security_audit.log"
    ], icon="[GUARD]")

    draw_card(26, 7, 23, 58, c_compiler, "Sandboxed Compiler Service", "compiler_service.py • MinGW / Clang", [
        ("• Isolated GCC/Clang Engine", [
            "Flags: -Wall -Wextra -std=c++17 -g",
            "Strict timeout enforcement (5.0s)",
            "Restricted RAM / process environment",
            "Safe temp file isolation (temp_program.exe)"
        ]),
        ("• Deterministic Tri-Path Dispatcher", [
            "Path A (Exit 0): Clean Run / Success",
            "  -> Execution stdout capture & display",
            "  -> Complexity & performance analysis",
            "Path B (Exit 2): Runtime Crash / SegFault",
            "  -> Triggers Agentic LLDB Debug Loop",
            "Path C (Exit 1): Compilation Error",
            "  -> Error classifier extracts error lines",
            "Path D (Exit -1): Timeout / Hang",
            "  -> Identifies infinite loops / O(N^k)"
        ]),
        ("• Output Sanitizer & Capture", [
            "Captures stdout, stderr, exit codes",
            "Prevents runaway memory buffer allocation"
        ])
    ], icon="[COMPILER]")

    # ─────────────────────────────────────────────────────────────────────────────
    # COLUMN 3: MULTI-AGENT ORCHESTRATION & ANALYSIS (x: 51 to 74)
    # ─────────────────────────────────────────────────────────────────────────────
    draw_card(51, 68, 23, 23, c_orch, "Multi-Agent Orchestrator", "orchestrator.py • Task Dispatcher", [
        "• Autonomous Coordinated Workflow:",
        "  - Receives compiler outcome & code state",
        "  - Dispatches tasks to specialized sub-agents",
        "  - Aggregates multi-agent insights & fixes",
        "• Sub-Agent Federation Hub:",
        "  - EfficiencyAgent: Big-O & bottlenecks",
        "  - SecurityAgent: Post-generation sanitization",
        "  - TestGeneratorAgent: Dynamic test generation",
        "  - AgentGuard: Runtime boundary protection"
    ], icon="[ORCHESTRATOR]")

    draw_card(51, 38, 23, 27, c_reason, "Deep Reasoning Engine", "reasoning/ Pipeline • Evidenced AI", [
        "• Four-Stage Evidence Grounding:",
        "  1. Fact Extraction: Collects exact line numbers,",
        "     variables, types, and compiler AST tokens.",
        "  2. Hypothesis Generation: Identifies root causes",
        "     (off-by-one, dangling pointer, null deref).",
        "  3. Refutation Check: Tests hypothesis against",
        "     source code constraints to eliminate bias.",
        "  4. Patch Synthesis: Emits minimal diff patch.",
        "• Profiles: Quick • Standard • Thorough"
    ], icon="[REASONING]")

    draw_card(51, 7, 23, 28, c_ui, "Complexity & Test Agents", "efficiency_agent.py • test_generator.py", [
        ("• ComplexityEngine (Theoretical & Real)", [
            "Static Big-O notation estimation (O(1), O(N), O(N^2))",
            "Loop nesting depth & branch factor analysis",
            "Auxiliary space complexity modeling"
        ]),
        ("• TestGeneratorAgent", [
            "Generates boundary test cases & edge inputs",
            "Empty containers, INT_MAX/MIN, scale arrays",
            "Validates user programs against edge inputs"
        ])
    ], icon="[AGENTS]")

    # ─────────────────────────────────────────────────────────────────────────────
    # COLUMN 4: AGENTIC LLDB & LOCAL AI INFERENCE (x: 76 to 98)
    # ─────────────────────────────────────────────────────────────────────────────
    draw_card(76, 53, 22, 38, c_agentic, "Agentic LLDB Engine", "lldb_engine.py • ReAct Debug Loop", [
        ("• Automated Subprocess Debugger", [
            "Spawns LLDB on crashing executable",
            "Captures crash signal (SIGSEGV, SIGABRT)",
            "Extracts backtrace (bt) & stack frame"
        ]),
        ("• Autonomous ReAct Tool Reasoner", [
            "Thought -> Action -> Observation loop",
            "frame variable inspection",
            "register read & memory address probing",
            "pinpoints exact offending pointer/index"
        ]),
        ("• Crash Synthesis & Patching", [
            "Produces root-cause analysis report",
            "Feeds precise memory dump into local LLM"
        ])
    ], icon="[LLDB]")

    draw_card(76, 7, 22, 43, c_llm, "Local AI Inference Engine", "Ollama Backend • 100% Offline", [
        ("• Dedicated C++ Coder Model", [
            "qwen2.5-coder:7b (Primary Engine)",
            "Zero cloud latency • Zero data transmission",
            "Complete local privacy for student code"
        ]),
        ("• Post-Execution Fix Engine", [
            "validate_ai_fix() verifies generated patch",
            "Ensures AI fix doesn't introduce vulnerabilities",
            "Formats code in clean markdown blocks"
        ]),
        ("• Semantic Analyzer (AST)", [
            "Semantic intent analysis against standard STL",
            "Suggests idiomatic C++ improvements"
        ]),
        ("• Multi-Turn Memory Context", [
            "Retains prior questions & iterations",
            "Context-aware interactive tutor assistant"
        ])
    ], icon="[LOCAL-LLM]")

    # ─────────────────────────────────────────────────────────────────────────────
    # CONNECTING ARROWS & DATA FLOW
    # ─────────────────────────────────────────────────────────────────────────────
    # 1. UI -> Pre-Execution Guardrail
    draw_arrow(24, 78, 26, 78, c_ui, "Source Code", text_pos=0.5)

    # 2. Guardrail Block -> Back to UI
    draw_arrow(26, 72, 24, 72, '#EF4444', "Blocked (CWE)", text_pos=0.5)

    # 3. Guardrail Safe -> Compiler Service
    draw_arrow(37.5, 68, 37.5, 65, c_compiler, "Safe Code", text_pos=0.5)

    # 4. Compiler -> Multi-Agent Orchestrator
    draw_arrow(49, 78, 51, 78, c_orch, "Compiler Result", text_pos=0.5)

    # 5. Compiler Path B -> Agentic LLDB
    draw_arrow(49, 36, 76, 70, c_agentic, "Exit 2 (Crash / SIGSEGV)", text_pos=0.6, rad=-0.12)

    # 6. LLDB <-> Local LLM (ReAct loop)
    draw_arrow(87, 53, 87, 50, c_agentic, "ReAct Steps", text_pos=0.5)

    # 7. Orchestrator -> Reasoning Engine
    draw_arrow(62.5, 68, 62.5, 65, c_reason, "Facts Extraction", text_pos=0.5)

    # 8. Reasoning Engine -> Complexity & Test Agents
    draw_arrow(62.5, 38, 62.5, 35, c_ui, "Task Delegation", text_pos=0.5)

    # 9. Orchestrator & Reasoning -> Local LLM
    draw_arrow(74, 51, 76, 45, c_llm, "Prompt / Context", text_pos=0.5, rad=-0.08)

    # 10. Local LLM -> UI (Verified Patch)
    draw_arrow(76, 25, 24, 60, c_llm, "Verified Code Fix & Tutor Advice", text_pos=0.45, rad=0.18)

    # 11. Compiler -> UI (Direct Output)
    draw_arrow(26, 50, 24, 56, c_compiler, "stdout / stderr", text_pos=0.5)

    # 12. Compiler & Runs -> Observability & Telemetry
    draw_arrow(26, 25, 24, 25, c_green, "Hardware Telemetry (kWh / CO2)", text_pos=0.5)

    # ─────────────────────────────────────────────────────────────────────────────
    # FOOTER & LEGEND BAR
    # ─────────────────────────────────────────────────────────────────────────────
    leg_box = FancyBboxPatch((2, 0.8), 96, 4.4, boxstyle="round,pad=0.1,rounding_size=0.6",
                             facecolor="#090E1A", edgecolor="#1E293B", linewidth=1.2, zorder=3)
    ax.add_patch(leg_box)

    ax.text(50, 4.0, "SYSTEM SUBSYSTEM COLOR LEGEND & EXECUTION CHANNELS",
            fontsize=9.5, fontweight='heavy', color='#94A3B8', ha='center', va='center', zorder=5, fontfamily='sans-serif')

    legends = [
        (c_ui, "UI & IDE Studio"),
        (c_security, "Pre-Scan Security"),
        (c_compiler, "Sandboxed Compiler"),
        (c_orch, "Multi-Agent Orchestrator"),
        (c_reason, "Deep Reasoning"),
        (c_agentic, "Agentic LLDB (ReAct)"),
        (c_llm, "Offline Ollama LLM"),
        (c_green, "Telemetry & Memory")
    ]

    start_x = 3.5
    spacing = 12.0
    for i, (col, text) in enumerate(legends):
        lx = start_x + i * spacing
        sq = FancyBboxPatch((lx, 1.4), 1.8, 1.4, boxstyle="round,pad=0.05,rounding_size=0.3",
                            facecolor=col, edgecolor="none", zorder=5)
        ax.add_patch(sq)
        ax.text(lx + 2.3, 2.1, text, fontsize=8.0, fontweight='bold', color='#CBD5E1', va='center', zorder=5, fontfamily='sans-serif')

    plt.tight_layout()

    out_dir = r"c:\TOACD_PROJECT_FINAL\TOACD_PROJECT\TOACD_PROJECT\Chatbot-Driven-Compiler-Debugger"
    out_file = os.path.join(out_dir, "architecture_diagram.png")
    plt.savefig(out_file, dpi=200, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()

    # Also copy to brain artifact directory for markdown display
    brain_dir = r"C:\Users\Arpit\.gemini\antigravity\brain\152b7375-db61-4a77-915a-4e70ce5ad07e"
    brain_out = os.path.join(brain_dir, "architecture_diagram.png")
    try:
        shutil.copy2(out_file, brain_out)
        print(f"Copied to artifact directory: {brain_out}")
    except Exception as e:
        print(f"Warning copying to brain: {e}")

    print(f"Generated high-resolution architecture diagram at: {out_file}")
    return out_file

if __name__ == "__main__":
    generate_system_architecture()
