"""
ui_themes.py: drop-in UI layer for the Chatbot-Driven C++ Compiler & Debugger.

Goals:
  1. Whole app fits ONE screen: header (56px) + two panes that scroll internally.
     The page itself never scrolls, and the chat input stays pinned at the bottom.
  2. Three switchable themes: Forest Dev (dark green), Midnight (dark blue), Daylight (light).
  3. Clean pixel-faithful reproduction of modern IDE cards without clutter.
"""

import streamlit as st

THEMES = {
    # Dark green-slate theme: editor feel, mint accent with amber highlights.
    "Forest Dev": dict(
        scheme="dark", editor="nord_dark",
        bg="#0F1714", surface="#16211C", surface2="#1E2D26", border="#2C4036",
        text="#E4EEE8", muted="#93A89D",
        accent="#3DDC97", on_accent="#06150E", accent2="#F5B84B",
        ok="#3DDC97", ok_bg="rgba(61, 220, 151, 0.08)",
        warn="#F5B84B", warn_bg="rgba(245, 184, 75, 0.1)",
        err="#FF7A7A", err_bg="rgba(255, 122, 122, 0.1)",
        code_bg="#111B16",
        shadow="none",
    ),
    # Dark blue-ink theme: soft periwinkle accent, easy on the eyes at night.
    "Midnight": dict(
        scheme="dark", editor="tomorrow_night",
        bg="#0B0F19", surface="#121826", surface2="#1A2338", border="#24324F",
        text="#E7ECF7", muted="#8D9BB5",
        accent="#60A5FA", on_accent="#08101E", accent2="#5EEAD4",
        ok="#4ADE80", ok_bg="rgba(74, 222, 128, 0.08)",
        warn="#FBBF24", warn_bg="rgba(251, 191, 36, 0.1)",
        err="#FF8A8A", err_bg="rgba(255, 138, 138, 0.1)",
        code_bg="#101728",
        shadow="none",
    ),
    # Clean light theme: cool paper background, cobalt accent.
    "Daylight": dict(
        scheme="light", editor="xcode",
        bg="#F0F4F8", surface="#FFFFFF", surface2="#F1F4F9", border="#C8D4E0",
        text="#18212F", muted="#5A6779",
        accent="#2563EB", on_accent="#FFFFFF", accent2="#0E8F8D",
        ok="#14803C", ok_bg="#E7F6EC",
        warn="#8A5B00", warn_bg="#FFF3D1",
        err="#C62828", err_bg="#FDE8E8",
        code_bg="#FFFFFF",
        shadow="0 1px 4px rgba(20,30,60,.06), 0 4px 16px rgba(20,30,60,.05)",
    ),
}
DEFAULT_THEME = "Forest Dev"

_BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp { background: var(--bg) !important; color: var(--text); }
.stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stMarkdownContainer"] { font-family: 'Manrope', system-ui, sans-serif; color: var(--text); }
.stApp small, .stApp [data-testid="stCaptionContainer"] { color: var(--muted) !important; }

/* ---------- Header chrome, deploy button (hide junk, keep sidebar) ---------- */
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stAppDeployButton"], .stDeployButton, footer, #MainMenu { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
.block-container { padding: 0.75rem 1.2rem 0 1.2rem !important; max-width: 100% !important; }
[data-testid="stAppViewContainer"] > .main, section.main { overflow: hidden; }

/* ---------- Sidebar: themed, not hidden ---------- */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
    color: var(--text) !important;
    font-family: 'Manrope', system-ui, sans-serif;
}
/* ---------- Form Inputs & Textareas (Global & Stdin Tab) ---------- */
[data-baseweb="select"] > div,
input,
.stTextInput input,
[data-baseweb="base-input"] {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-color: var(--border) !important;
    border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.9rem !important;
    caret-color: var(--accent) !important;
}

/* Stdin Text Area - high-contrast black text on white background */
.ui-stdin-wrapper [data-testid="stTextArea"] textarea,
.ui-stdin-wrapper textarea,
[data-testid="stTextArea"] textarea,
div[data-baseweb="textarea"] textarea {
    background: #ffffff !important;
    background-color: #ffffff !important;
    color: #000000 !important;
    -webkit-text-fill-color: #000000 !important;
    caret-color: #000000 !important;
    font-family: 'JetBrains Mono', Consolas, monospace !important;
    font-size: 0.92rem !important;
    font-weight: 600 !important;
    border: 1.5px solid #cbd5e1 !important;
    border-radius: 8px !important;
}

.ui-stdin-wrapper textarea::placeholder,
[data-testid="stTextArea"] textarea::placeholder,
div[data-baseweb="textarea"] textarea::placeholder {
    color: #64748b !important;
    -webkit-text-fill-color: #64748b !important;
    font-weight: 400 !important;
    opacity: 0.85 !important;
}

.ui-stdin-wrapper textarea:focus,
[data-testid="stTextArea"] textarea:focus,
div[data-baseweb="textarea"] textarea:focus {
    border-color: #3ddc97 !important;
    box-shadow: 0 0 0 2px rgba(61, 220, 151, 0.3) !important;
    outline: none !important;
}
[data-testid="stSidebar"] .stCheckbox > label { color: var(--text) !important; }
[data-testid="stSidebar"] [data-baseweb="slider"] > div > div { background: var(--accent) !important; }

/* ---------- Global Expanders (Main Page & Sidebar) ---------- */
[data-testid="stExpander"],
div[data-testid="stExpander"],
[data-testid="stExpander"] details {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary {
    color: var(--text) !important;
    font-weight: 600 !important;
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    border-radius: 12px !important;
    padding: 0.5rem 0.85rem !important;
    transition: background-color 140ms ease-out !important;
}
[data-testid="stExpander"] summary:hover {
    background: var(--surface) !important;
    color: var(--accent) !important;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    border-top: 1px solid var(--border) !important;
    padding: 0.85rem 1rem !important;
}

/* ---------- Global Alerts & Banners (st.info, st.warning, st.error) ---------- */
div[data-testid="stAlert"] {
    background: var(--surface) !important;
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
}
div[data-testid="stAlert"] p,
div[data-testid="stAlert"] span,
div[data-testid="stAlert"] div {
    color: var(--text) !important;
}

/* ---------- Rating Breakdown Deduction Cards ---------- */
.ui-deduction-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.85rem 1.1rem;
    margin-bottom: 0.75rem;
    transition: border-color 150ms ease;
}
.ui-deduction-card:hover {
    border-color: var(--accent);
}
.ui-deduction-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
}
.ui-deduction-title {
    font-weight: 700;
    font-size: 0.95rem;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 0.45rem;
}
.ui-deduction-badge {
    font-size: 0.76rem;
    font-weight: 700;
    padding: 0.2rem 0.65rem;
    border-radius: 9999px;
    letter-spacing: 0.02em;
}
.ui-deduction-badge.efficiency {
    background: rgba(245, 184, 75, 0.15);
    color: #F5B84B;
    border: 1px solid rgba(245, 184, 75, 0.35);
}
.ui-deduction-badge.security-warn {
    background: rgba(245, 184, 75, 0.15);
    color: #F5B84B;
    border: 1px solid rgba(245, 184, 75, 0.35);
}
.ui-deduction-badge.security-crit {
    background: rgba(255, 107, 107, 0.15);
    color: #FF6B6B;
    border: 1px solid rgba(255, 107, 107, 0.35);
}
.ui-deduction-why {
    font-size: 0.86rem;
    color: var(--muted);
    line-height: 1.5;
    margin-bottom: 0.45rem;
}
.ui-deduction-why strong {
    color: var(--text);
}
.ui-deduction-fix {
    font-size: 0.86rem;
    color: var(--text);
    line-height: 1.5;
    margin-bottom: 0.4rem;
}
.ui-deduction-fix strong {
    color: var(--accent);
}
.ui-deduction-code {
    background: var(--code_bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    color: var(--accent);
    margin-top: 0.35rem;
    white-space: pre-wrap;
    overflow-x: auto;
}

/* ---------- Agentic AI Execution Trace & Deliberation Cards ---------- */
.ui-agent-trace-container {
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
    margin-top: 0.75rem;
    margin-bottom: 0.75rem;
}
.ui-agent-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-left: 3.5px solid var(--accent);
    border-radius: 8px;
    padding: 0.7rem 0.95rem;
    transition: transform 120ms ease, box-shadow 120ms ease;
}
.ui-agent-card.planner {
    border-left-color: #38bdf8;
}
.ui-agent-card.optimizer {
    border-left-color: #a855f7;
}
.ui-agent-card.judge-success {
    border-left-color: #10b981;
}
.ui-agent-card.judge-warn {
    border-left-color: #f59e0b;
}
.ui-agent-card.critic {
    border-left-color: #ef4444;
}
.ui-agent-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.35rem;
}
.ui-agent-tag {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.2rem 0.55rem;
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.05);
}
.ui-agent-tag.planner { color: #38bdf8; background: rgba(56, 189, 248, 0.12); }
.ui-agent-tag.optimizer { color: #c084fc; background: rgba(192, 132, 252, 0.12); }
.ui-agent-tag.judge { color: #34d399; background: rgba(52, 211, 153, 0.12); }
.ui-agent-tag.critic { color: #f87171; background: rgba(248, 113, 113, 0.12); }
.ui-agent-action {
    font-weight: 600;
    font-size: 0.88rem;
    color: var(--text);
    margin-left: 0.4rem;
}
.ui-agent-time {
    font-size: 0.76rem;
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
}
.ui-agent-details {
    font-size: 0.82rem;
    color: var(--text);
    opacity: 0.9;
    line-height: 1.45;
    white-space: pre-wrap;
    background: rgba(0, 0, 0, 0.15);
    padding: 0.45rem 0.65rem;
    border-radius: 6px;
    margin-top: 0.35rem;
    border: 1px solid rgba(255, 255, 255, 0.04);
}
.ui-agent-metric-delta {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.35rem 0.65rem;
    border-radius: 8px;
    background: rgba(61, 220, 151, 0.1);
    border: 1px solid rgba(61, 220, 151, 0.3);
    color: #3ddc97;
    font-weight: 700;
    font-size: 0.85rem;
}
[data-testid="collapsedControl"] {
    color: var(--muted) !important;
    background: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
}

/* ---------- Header bar ---------- */
.app-bar { display: flex; align-items: center; gap: 0.85rem; height: 38px; }
.app-title { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.02em; color: var(--text); }
.app-chip { font-size: 0.76rem; font-weight: 600; padding: 0.22rem 0.75rem; border-radius: 999px;
            background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }
.app-chip.live { color: var(--accent); border-color: var(--accent); background: transparent; }

/* ---------- Two fixed-height cards side-by-side ---------- */
div[data-testid="stHorizontalBlock"] { gap: 1.25rem !important; }

div[data-testid="stColumn"]:has(.pane-left), div[data-testid="column"]:has(.pane-left),
div[data-testid="stColumn"]:has(.pane-right), div[data-testid="column"]:has(.pane-right) {
    height: calc(100vh - 66px) !important;
    overflow-y: auto !important;
    overflow-x: hidden !important;
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 16px !important;
    padding: 1.1rem 1.2rem !important;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
    display: flex !important;
    flex-direction: column !important;
}

/* Ace editor iframe & wrapper rounded styling without harsh borders */
div[data-testid="stCustomComponentV1"],
div[data-testid="stCustomComponentV1"] > iframe,
iframe[src*="streamlit_ace"],
iframe[title*="streamlit_ace"],
iframe {
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    background: var(--code_bg) !important;
    box-shadow: none !important;
    outline: none !important;
}

/* Status Banner */
.status {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.55rem 0.95rem;
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    margin: 0.55rem 0 0.65rem 0;
}
.status.ok {
    background: var(--ok_bg);
    color: var(--ok);
    border: 1.5px solid var(--ok);
}
.status.err {
    background: var(--err_bg);
    color: var(--err);
    border: 1.5px solid var(--err);
}

/* Underline Tabs */
div[data-baseweb="tab-list"] {
    gap: 1.4rem !important;
    border-bottom: 1px solid var(--border) !important;
    background: transparent !important;
}
button[data-baseweb="tab"] {
    color: var(--muted) !important;
    font-size: 0.92rem !important;
    font-weight: 600 !important;
    padding: 0.35rem 0.2rem 0.55rem 0.2rem !important;
    border: none !important;
    background: transparent !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--accent) !important;
}
[data-baseweb="tab-highlight"] {
    background: var(--accent) !important;
    height: 2px !important;
}
div[data-baseweb="tab-panel"] {
    padding: 0.6rem 0 0.4rem 0 !important;
}

/* ===== BASE BUTTON RESET =====
   All regular (non-primary) Streamlit buttons get theme surface colors. */
.stButton > button {
    background: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-family: 'Manrope', system-ui, sans-serif !important;
    transition: border-color .15s, color .15s, background .15s !important;
    box-shadow: none !important;
}
.stButton > button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--surface2) !important;
}

/* Primary compile button */
.stButton > button[kind="primary"], .stButton > button[data-testid="stBaseButton-primary"] {
    background: var(--accent) !important;
    color: var(--on_accent) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    padding: 0.65rem 1rem !important;
    margin-top: 0.35rem !important;
    width: 100% !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"]:hover {
    filter: brightness(1.1) !important;
    color: var(--on_accent) !important;
}

/* ===== PILL BUTTONS (Explain/Debug/Optimize/Tests) =====
   Target by Streamlit's data-testid key attribute. */
button[data-testid="stBaseButton-secondary"][key="pill_explain"],
button[data-testid="stBaseButton-secondary"][key="pill_debug"],
button[data-testid="stBaseButton-secondary"][key="pill_optimize"],
button[data-testid="stBaseButton-secondary"][key="pill_tests"],
[data-testid="stButton-pill_explain"] > button,
[data-testid="stButton-pill_debug"] > button,
[data-testid="stButton-pill_optimize"] > button,
[data-testid="stButton-pill_tests"] > button {
    border-radius: 9999px !important;
    background: transparent !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.87rem !important;
    padding: 0.28rem 0.8rem !important;
}
[data-testid="stButton-pill_explain"] > button:hover,
[data-testid="stButton-pill_debug"] > button:hover,
[data-testid="stButton-pill_optimize"] > button:hover,
[data-testid="stButton-pill_tests"] > button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: transparent !important;
}
/* Explain always highlighted (active) */
[data-testid="stButton-pill_explain"] > button {
    border: 1.5px solid var(--accent) !important;
    color: var(--accent) !important;
    background: transparent !important;
}

/* Chat Messages */
[data-testid="stChatMessage"] {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    padding: 0.85rem 1.15rem !important;
    margin-bottom: 0.65rem !important;
    max-height: none !important;
}
[data-testid="stChatMessage"] [data-testid="stChatMessageAvatar"] {
    display: none !important;
}
[data-testid="stChatMessage"] p {
    color: var(--text) !important;
    font-size: 0.92rem !important;
    line-height: 1.6 !important;
    margin: 0.3rem 0 !important;
}
[data-testid="stChatMessage"] ol {
    padding-left: 1.25rem !important;
    margin: 0.35rem 0 !important;
}
[data-testid="stChatMessage"] ol li {
    color: var(--text) !important;
    margin-bottom: 0.3rem !important;
    font-size: 0.92rem !important;
}
[data-testid="stChatMessage"] ol li::marker {
    color: var(--accent) !important;
    font-weight: 700 !important;
}
[data-testid="stChatMessage"] ul {
    padding-left: 1.25rem !important;
    margin: 0.35rem 0 !important;
}
[data-testid="stChatMessage"] ul li {
    color: var(--text) !important;
    margin-bottom: 0.3rem !important;
    font-size: 0.92rem !important;
}
[data-testid="stChatMessage"] ul li::marker {
    color: var(--accent) !important;
}

/* Headings inside chat responses */
[data-testid="stChatMessage"] h1,
[data-testid="stChatMessage"] h2,
[data-testid="stChatMessage"] h3,
[data-testid="stChatMessage"] h4 {
    color: var(--text) !important;
    font-weight: 700 !important;
    margin-top: 0.75rem !important;
    margin-bottom: 0.35rem !important;
    letter-spacing: -0.01em;
}
[data-testid="stChatMessage"] h2 {
    font-size: 1.12rem !important;
    color: var(--accent) !important;
}
[data-testid="stChatMessage"] h3 {
    font-size: 1.02rem !important;
}
/* Hide anchor link chain icons in chat headers */
[data-testid="stChatMessage"] a[href^="#"],
[data-testid="stChatMessage"] .header-anchor,
[data-testid="stChatMessage"] a:has(svg),
[data-testid="stChatMessage"] h1 a,
[data-testid="stChatMessage"] h2 a,
[data-testid="stChatMessage"] h3 a,
[data-testid="stChatMessage"] h4 a {
    display: none !important;
}

/* Inline Code (e.g. `sum`, `0`, `int`) */
.stApp code:not(pre code),
[data-testid="stMarkdownContainer"] code:not(pre code),
[data-testid="stChatMessage"] code:not(pre code),
[data-testid="stChatMessage"] p code,
[data-testid="stChatMessage"] li code {
    background: var(--code_bg) !important;
    color: var(--accent) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    padding: 0.15rem 0.45rem !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.88em !important;
    font-weight: 500 !important;
    box-shadow: none !important;
}

/* LaTeX Math / KaTeX equations & symbols */
.katex,
.katex-html,
.katex-display,
[data-testid="stChatMessage"] .katex {
    color: var(--text) !important;
    background: transparent !important;
    font-size: 1.02em !important;
}
.katex .base {
    color: var(--text) !important;
}
.katex .mord,
.katex .mbin,
.katex .mrel,
.katex .mpunct,
.katex .minner {
    color: var(--text) !important;
}
.katex .mord.mathnormal {
    color: var(--accent) !important;
}
.katex-display {
    padding: 0.4rem 0 !important;
    margin: 0.4rem 0 !important;
    overflow-x: auto;
}

/* Code Blocks in Chat & Markdown */
[data-testid="stCode"],
.stCodeBlock,
[data-testid="stChatMessage"] pre,
[data-testid="stChatMessage"] [data-testid="stCode"],
[data-testid="stMarkdownContainer"] pre {
    background: var(--code_bg) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    box-shadow: none !important;
    margin: 0.75rem 0 !important;
}

[data-testid="stCode"] pre,
.stCodeBlock pre,
[data-testid="stChatMessage"] pre code,
[data-testid="stMarkdownContainer"] pre code {
    background: var(--code_bg) !important;
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.88rem !important;
    line-height: 1.6 !important;
    border: none !important;
    padding: 0.85rem 1rem !important;
}

[data-testid="stCode"] button,
.stCodeBlock button {
    background: var(--surface2) !important;
    color: var(--muted) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
}
[data-testid="stCode"] button:hover,
.stCodeBlock button:hover {
    color: var(--text) !important;
    border-color: var(--accent) !important;
}

/* Sticky Chat Input */
[data-testid="stChatInput"] {
    position: sticky !important;
    bottom: 0 !important;
    background: var(--surface) !important;
    padding-top: 0.4rem !important;
    z-index: 10 !important;
}
[data-testid="stChatInput"] > div {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
}
[data-testid="stChatInput"] textarea {
    font-family: 'Manrope', system-ui, sans-serif !important;
    font-size: 0.9rem !important;
    color: var(--text) !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: var(--muted) !important;
}

/* ===== MODERN DIAGNOSTICS PANEL ===== */
.diag-container {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
    margin-top: 0.35rem;
    margin-bottom: 0.5rem;
}
.diag-header-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 0.45rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 0.15rem;
}
.diag-title {
    font-size: 0.88rem;
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.01em;
}
.diag-pills-wrap {
    display: flex;
    align-items: center;
    gap: 0.4rem;
}
.diag-pill {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.18rem 0.6rem;
    border-radius: 9999px;
    letter-spacing: 0.02em;
}
.diag-pill-error {
    background: var(--err_bg) !important;
    color: var(--err) !important;
    border: 1px solid var(--err) !important;
}
.diag-pill-warning {
    background: var(--warn_bg) !important;
    color: var(--warn) !important;
    border: 1px solid var(--warn) !important;
}
.diag-pill-info {
    background: var(--surface2) !important;
    color: var(--muted) !important;
    border: 1px solid var(--border) !important;
}

.diag-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.7rem 0.9rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
}
.diag-card-error {
    border-left: 3.5px solid var(--err) !important;
    background: var(--err_bg) !important;
    border-color: var(--border);
}
.diag-card-warning {
    border-left: 3.5px solid var(--warn) !important;
    background: var(--warn_bg) !important;
    border-color: var(--border);
}
.diag-card-info {
    border-left: 3.5px solid var(--accent) !important;
    background: var(--surface2) !important;
    border-color: var(--border);
}
.diag-card-success {
    border-left: 3.5px solid var(--ok) !important;
    background: var(--ok_bg) !important;
    border-color: var(--border);
}

.diag-card-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.diag-badge {
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.04em;
    padding: 0.15rem 0.5rem;
    border-radius: 6px;
    text-transform: uppercase;
}
.diag-badge-error {
    background: var(--err);
    color: #FFFFFF;
}
.diag-badge-warning {
    background: var(--warn);
    color: #1A1A1A;
}
.diag-badge-info {
    background: var(--accent);
    color: #FFFFFF;
}
.diag-badge-success {
    background: var(--ok);
    color: #FFFFFF;
}

.diag-line-tag {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--muted);
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 0.12rem 0.45rem;
    border-radius: 5px;
}

.diag-msg {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.86rem;
    line-height: 1.45;
    color: var(--text);
    word-break: break-word;
}

.diag-details-box {
    margin-top: 0.2rem;
    padding: 0.45rem 0.65rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
}
.diag-detail-row {
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--text);
}
.diag-detail-row.fix {
    color: var(--accent);
    font-weight: 600;
}
.diag-detail-lbl {
    font-weight: 700;
    color: var(--muted);
}
.diag-detail-val {
    font-weight: 500;
}

/* Backward compatibility for severity blocks / chips */
.ui-status {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.65rem 0.85rem;
    margin: 0.4rem 0;
    background: var(--surface2);
    display: flex;
    gap: 0.6rem;
    align-items: flex-start;
}
.ui-status-error { border-left: 3.5px solid var(--err) !important; background: var(--err_bg) !important; }
.ui-status-warning { border-left: 3.5px solid var(--warn) !important; background: var(--warn_bg) !important; }
.ui-status-success { border-left: 3.5px solid var(--ok) !important; background: var(--ok_bg) !important; }
.ui-status-heading { font-weight: 700; font-size: 0.82rem; margin-bottom: 0.2rem; }
.ui-status-message { font-size: 0.88rem; color: var(--text); }
.ui-chip {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface2);
}

/* Theme Selectbox & Dropdown Popover */
div[data-baseweb="select"] {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
}
div[data-baseweb="select"] > div {
    background: transparent !important;
    border: none !important;
    color: var(--text) !important;
    font-weight: 600 !important;
}
div[data-baseweb="select"] * {
    color: var(--text) !important;
    background: transparent !important;
    fill: var(--text) !important;
}
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
ul[role="listbox"],
[data-baseweb="menu"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    box-shadow: 0 8px 30px rgba(0,0,0,0.5) !important;
    padding: 4px !important;
    z-index: 999999 !important;
}
li[role="option"], [data-baseweb="menu"] li {
    background: var(--surface) !important;
    color: var(--text) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    padding: 0.5rem 0.85rem !important;
    margin: 2px 0 !important;
    cursor: pointer !important;
}
li[role="option"]:hover, [data-baseweb="menu"] li:hover,
li[role="option"][aria-selected="true"] {
    background: var(--surface2) !important;
    color: var(--accent) !important;
}

@media (max-width: 900px) {
  [data-testid="stAppViewContainer"] > .main, section.main { overflow: auto; }
  div[data-testid="stColumn"]:has(.pane-left), div[data-testid="column"]:has(.pane-left),
  div[data-testid="stColumn"]:has(.pane-right), div[data-testid="column"]:has(.pane-right) {
      height: auto !important; overflow: visible !important; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""


def _vars_css(t: dict) -> str:
    body = ";".join(f"--{k}:{v}" for k, v in t.items() if k not in ("editor",))
    return f":root{{{body}}} .stApp{{color-scheme:{t['scheme']};}}"


def get_theme() -> dict:
    theme_key = st.session_state.get("ui_theme", DEFAULT_THEME)
    return THEMES.get(theme_key, THEMES[DEFAULT_THEME])


def pane_marker(side: str) -> None:
    """Call as the first line inside each column: side = 'left' or 'right'."""
    st.markdown(f'<span class="pane-{side}"></span>', unsafe_allow_html=True)


def status_banner(kind: str, text: str) -> None:
    """kind = 'ok' | 'warn' | 'err'. Renders sleek status pill matching the mockups."""
    icon = {"ok": "✓", "warn": "!", "err": "✖"}[kind]
    st.markdown(f'<div class="status {kind}"><span>{icon}</span><span>{text}</span></div>',
                unsafe_allow_html=True)


def setup_ui(model_name: str | None = None, provider: str | None = None) -> dict:
    """Renders the slim header (title, chips, theme picker), injects CSS, returns the theme dict."""
    if "ui_theme" not in st.session_state or st.session_state.ui_theme not in THEMES:
        st.session_state.ui_theme = DEFAULT_THEME

    # Dynamic model resolution: never hardcode any model
    if not model_name:
        model_name = (
            st.session_state.get("last_served_model")
            or st.session_state.get("ollama_live_model_select")
            or os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b")
        )

    title, picker = st.columns([4.8, 1.5], vertical_alignment="center")
    with title:
        prov_chip = f'<span class="app-chip">{provider.capitalize()}</span>' if provider else ''
        st.markdown(
            f'<div class="app-bar"><span class="app-title">Chatbot Compiler Debugger</span>'
            f'<span class="app-chip live"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent);margin-right:6px;vertical-align:middle;"></span>{model_name}</span>'
            f'{prov_chip}'
            f'<span class="app-chip">🛡️ Sandboxed run</span></div>',
            unsafe_allow_html=True)
    with picker:
        theme_names = list(THEMES.keys())
        current_theme = st.session_state.get("ui_theme", DEFAULT_THEME)
        st.selectbox("Theme", theme_names, key="ui_theme", label_visibility="collapsed",
                     index=theme_names.index(current_theme) if current_theme in theme_names else 0)
    theme = get_theme()
    editor_ace_theme = f"ace/theme/{theme['editor']}"
    st.markdown(f"<style>{_vars_css(theme)}{_BASE_CSS}</style>", unsafe_allow_html=True)
    # Inject the chosen ace theme name into window so syncAceTheme in ui_theme.py respects it
    st.html(f'<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" '
            f'onload="try{{window.parent.__aceThemeName=\'{editor_ace_theme}\';}}catch(e){{}}this.remove();" '
            f'style="display:none;">')
    return theme
