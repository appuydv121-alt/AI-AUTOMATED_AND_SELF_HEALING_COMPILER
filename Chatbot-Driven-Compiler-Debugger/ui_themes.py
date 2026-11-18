"""
ui_themes.py: drop-in UI layer for the Chatbot-Driven C++ Compiler & Debugger.

Goals:
  1. Whole app fits ONE screen: header (56px) + two panes that scroll internally.
     The page itself never scrolls, and the chat input stays pinned at the bottom.
  2. Three switchable themes: Forest Dev (dark green), Midnight (dark blue), Daylight (light).
  3. Clean pixel-faithful reproduction of modern IDE cards without clutter.
"""

import streamlit as st
import streamlit.components.v1 as components

THEMES = {
    # Studio Light: World-class Apple / Stripe / Linear high-contrast light IDE theme.
    "Studio Light": dict(
        scheme="light", editor="chrome",
        bg="#F1F5F9",              # Slate-100 canvas (provides instant high card boundary contrast!)
        surface="#FFFFFF",         # Pure crisp white cards
        surface2="#F8FAFC",        # Smooth slate-50 wells & secondary cards
        border="#CBD5E1",          # Defined slate-300 boundary border
        text="#0F172A",            # Deep slate-900 high contrast text
        muted="#64748B",           # Slate-500 secondary text
        accent="#2563EB",          # Electric Royal Blue
        on_accent="#FFFFFF",       # Pure white
        accent2="#3B82F6",         # Smooth bright blue gradient partner
        ok="#059669",              # Emerald 600
        ok_bg="#ECFDF5",           # Emerald 50 soft tint
        warn="#D97706",            # Amber 600
        warn_bg="#FFFBEB",         # Amber 50 soft tint
        err="#DC2626",             # Red 600
        err_bg="#FEF2F2",          # Rose 50 soft tint
        code_bg="#FFFFFF",         # Pure white editor surface
        shadow="0 10px 25px -3px rgba(15, 23, 42, 0.12), 0 4px 8px -2px rgba(15, 23, 42, 0.08), 0 0 0 1.5px rgba(15, 23, 42, 0.06)",
    ),
    # Flagship Dark theme: Sleek obsidian slate, vibrant cyan accent, seamless editor.
    "Cyber Slate": dict(
        scheme="dark", editor="tomorrow_night",
        bg="#0B0F17", surface="#111827", surface2="#1E293B", border="#243047",
        text="#F8FAFC", muted="#94A3B8",
        accent="#38BDF8", on_accent="#0B0F17", accent2="#818CF8",
        ok="#10B981", ok_bg="rgba(16, 185, 129, 0.12)",
        warn="#F59E0B", warn_bg="rgba(245, 158, 11, 0.12)",
        err="#EF4444", err_bg="rgba(239, 68, 68, 0.12)",
        code_bg="#0F172A",
        shadow="0 8px 24px rgba(0, 0, 0, 0.28)",
    ),
    # Tokyo Night: Ultra-popular developer theme with purple/electric blue tones.
    "Tokyo Night": dict(
        scheme="dark", editor="twilight",
        bg="#1A1B26", surface="#24283B", surface2="#2F3549", border="#414868",
        text="#C0CAF5", muted="#7AA2F7",
        accent="#7AA2F7", on_accent="#1A1B26", accent2="#BB9AF7",
        ok="#9ECE6A", ok_bg="rgba(158, 206, 106, 0.12)",
        warn="#E0AF68", warn_bg="rgba(224, 175, 104, 0.12)",
        err="#F7768E", err_bg="rgba(247, 118, 142, 0.12)",
        code_bg="#1F2335",
        shadow="0 8px 24px rgba(0, 0, 0, 0.35)",
    ),
    # One Dark Pro: Atom / VS Code iconic palette.
    "One Dark Pro": dict(
        scheme="dark", editor="tomorrow_night",
        bg="#1E1E24", surface="#282C34", surface2="#353B45", border="#3E4451",
        text="#ABB2BF", muted="#828997",
        accent="#61AFEF", on_accent="#1E1E24", accent2="#C678DD",
        ok="#98C379", ok_bg="rgba(152, 195, 121, 0.12)",
        warn="#E5C07B", warn_bg="rgba(229, 192, 123, 0.12)",
        err="#E06C75", err_bg="rgba(224, 108, 117, 0.12)",
        code_bg="#21252B",
        shadow="0 8px 24px rgba(0, 0, 0, 0.3)",
    ),
}
DEFAULT_THEME = "Studio Light"

_BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp { background: var(--bg) !important; color: var(--text); }
.stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stMarkdownContainer"] { font-family: 'Manrope', system-ui, sans-serif; color: var(--text); }
.stApp small, .stApp [data-testid="stCaptionContainer"] { color: var(--muted) !important; }

/* ---------- Header chrome, deploy button (hide junk, keep sidebar toggle accessible) ---------- */
header[data-testid="stHeader"] {
    background: transparent !important;
    height: 48px !important;
    z-index: 99999 !important;
    pointer-events: none !important;
}
header[data-testid="stHeader"] * {
    pointer-events: auto !important;
}
[data-testid="stAppDeployButton"], .stDeployButton, footer, #MainMenu { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
.block-container {
    padding: 0.75rem 1.2rem 0 1.2rem !important;
    max-width: 100% !important;
    width: 100% !important;
}
[data-testid="stAppViewContainer"] > .main, section.main {
    overflow: hidden !important;
    flex: 1 1 auto !important;
    width: auto !important;
    min-width: 0 !important;
    margin: 0 !important;
}

/* Keep sidebar toggle buttons clean, correctly positioned, and prevent icon font ligature leaks */
[data-testid="stSidebarHeader"] {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    padding: 0.4rem 0.75rem !important;
    background: var(--surface) !important;
    border-bottom: 1px solid var(--border) !important;
}
[data-testid="stSidebarCollapseButton"] {
    display: inline-flex !important;
    margin: 0 !important;
    padding: 0 !important;
}
[data-testid="stSidebarCollapseButton"] button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    background: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    width: 32px !important;
    height: 32px !important;
    padding: 0 !important;
    cursor: pointer !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
    transition: all 0.15s ease !important;
}
[data-testid="stSidebarCollapseButton"] button:hover {
    background: var(--surface) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}

/* Collapsed sidebar toggle button (visible in top-left when sidebar closed) */
[data-testid="collapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    z-index: 100000 !important;
    position: fixed !important;
    top: 10px !important;
    left: 10px !important;
}
[data-testid="collapsedControl"] button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    background: var(--surface) !important;
    color: var(--text) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: 8px !important;
    width: 36px !important;
    height: 36px !important;
    padding: 0 !important;
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.12) !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
}
[data-testid="collapsedControl"] button:hover {
    background: var(--surface2) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    transform: scale(1.05) !important;
}
}

/* Protect icon font ligatures from being broken into raw text */
[data-testid="stSidebarCollapseButton"] *,
[data-testid="collapsedControl"] *,
[data-testid="stSidebar"] span[class*="material"],
[data-testid="stSidebar"] span[data-testid*="icon"] {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', system-ui !important;
}

/* ---------- Anti-Shading / Completely Block Dimming, Fading & Stale Overlays ---------- */
[data-stale="true"],
[data-stale="true"] *,
div[data-stale="true"],
div[data-stale="true"] *,
.stApp [data-stale="true"],
.stApp [data-stale="true"] *,
div[data-testid="stAppViewContainer"] [data-stale="true"],
div[data-testid="stAppViewContainer"] [data-stale="true"] *,
.main [data-stale="true"],
.main [data-stale="true"] *,
div[data-testid="stAppViewContainer"],
section.main,
.stApp,
[data-testid="stAppViewBlockContainer"],
.element-container,
div[data-testid="stVerticalBlock"],
div[data-testid="stColumn"],
div[data-testid="column"],
div[data-testid="stHorizontalBlock"],
iframe[src*="streamlit_ace"],
iframe[title*="streamlit_ace"] {
    opacity: 1 !important;
    filter: none !important;
    -webkit-filter: none !important;
    transition: none !important;
}

/* Hide Streamlit status widget overlay */
div[data-testid="stStatusWidget"],
[data-testid="stStatusWidget"] {
    display: none !important;
    visibility: hidden !important;
}

/* ---------- Sidebar: cleanly structured, elegant IDE sidebar ---------- */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1.5px solid var(--border) !important;
    box-shadow: 4px 0 24px rgba(15, 23, 42, 0.08) !important;
    transition: width 0.2s ease, min-width 0.2s ease, max-width 0.2s ease !important;
}

/* Expanded state: fixed clean width */
[data-testid="stSidebar"][aria-expanded="true"],
section[data-testid="stSidebar"][aria-expanded="true"] {
    min-width: 320px !important;
    max-width: 340px !important;
    width: 330px !important;
    display: block !important;
    visibility: visible !important;
}

/* Minimized / Collapsed state: completely collapse to 0 width and release layout space */
[data-testid="stSidebar"][aria-expanded="false"],
section[data-testid="stSidebar"][aria-expanded="false"] {
    min-width: 0 !important;
    max-width: 0 !important;
    width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
    overflow: hidden !important;
    display: none !important;
}
[data-testid="stSidebarUserContent"] {
    padding: 0.6rem 1.1rem 1.5rem 1.1rem !important;
    overflow-x: hidden !important;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: var(--text) !important;
    font-family: 'Manrope', system-ui, sans-serif;
}
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] .stSubheader {
    font-size: 0.88rem !important;
    font-weight: 700 !important;
    color: var(--muted) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    margin-top: 1rem !important;
    margin-bottom: 0.4rem !important;
    padding-bottom: 0.25rem !important;
    border-bottom: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] label {
    font-size: 0.84rem !important;
    font-weight: 600 !important;
    color: var(--text) !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea {
    background: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] .stCheckbox {
    margin-bottom: 0.2rem !important;
}
[data-testid="stSidebar"] .stCheckbox > label {
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.84rem !important;
}
[data-testid="stSidebar"] [data-baseweb="slider"] > div > div {
    background: var(--accent) !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] details {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    color: var(--text) !important;
    font-weight: 600 !important;
}

/* ---------- Header bar ---------- */
.app-bar {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    height: 38px;
    flex-wrap: nowrap;
    overflow: hidden;
}
.app-title {
    font-size: 1.22rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: var(--text);
    white-space: nowrap;
}
.app-chip {
    font-size: 0.74rem;
    font-weight: 600;
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    background: var(--surface2);
    color: var(--muted);
    border: 1px solid var(--border);
    white-space: nowrap;
}
.app-chip.live {
    color: var(--accent);
    border-color: var(--accent);
    background: transparent;
}

/* ---------- Two fixed-height cards side-by-side ---------- */
div[data-testid="stHorizontalBlock"] { gap: 1.25rem !important; }

div[data-testid="stColumn"]:has(.pane-left), div[data-testid="column"]:has(.pane-left),
div[data-testid="stColumn"]:has(.pane-right), div[data-testid="column"]:has(.pane-right) {
    height: calc(100vh - 66px) !important;
    overflow-y: auto !important;
    overflow-x: hidden !important;
    background: var(--surface) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: 16px !important;
    padding: 1.15rem 1.25rem !important;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
    display: flex !important;
    flex-direction: column !important;
    box-shadow: 0 10px 25px -4px rgba(15, 23, 42, 0.08), 0 4px 8px -2px rgba(15, 23, 42, 0.05), 0 0 0 1px rgba(15, 23, 42, 0.04) !important;
}

/* Code editor: completely remove harsh dark/black outer borders & boxes */
div[data-testid="stCustomComponentV1"],
div[data-testid="stCustomComponentV1"] * {
    border: none !important;
    background: transparent !important;
    padding: 0 !important;
    margin: 0 !important;
    box-shadow: none !important;
    outline: none !important;
}

iframe[src*="streamlit_ace"],
iframe[title*="streamlit_ace"],
iframe[data-testid="stCustomComponentV1"],
div[data-testid="stCustomComponentV1"] iframe {
    border: none !important;
    border-radius: 8px !important;
    background: var(--code_bg) !important;
    box-shadow: none !important;
    outline: none !important;
}

/* =========================================================================
   INLINE CODE & BADGES (ELIMINATE ALL BLACK BOXES)
   ========================================================================= */
code,
.stApp code,
[data-testid="stMarkdownContainer"] code,
p code,
li code,
span code,
div code {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    color: var(--accent) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    padding: 0.15rem 0.42rem !important;
    font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace !important;
    font-size: 0.84rem !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03) !important;
}

/* Code blocks and pre elements (LLDB responses, terminal output, stack traces) */
pre,
.stApp pre,
[data-testid="stCode"],
[data-testid="stCode"] pre,
pre code {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace !important;
    font-size: 0.86rem !important;
    line-height: 1.45 !important;
}

div[data-testid="stCode"] {
    padding: 0.5rem !important;
}

/* Status Banner */
.status {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0.65rem 1rem;
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    margin: 0.6rem 0 0.7rem 0;
    box-shadow: 0 2px 6px rgba(15, 23, 42, 0.05) !important;
}
.status.ok {
    background: var(--ok_bg) !important;
    color: var(--ok) !important;
    border: 1.5px solid var(--ok) !important;
    border-left: 4.5px solid var(--ok) !important;
}
.status.err {
    background: var(--err_bg) !important;
    color: var(--err) !important;
    border: 1.5px solid var(--err) !important;
    border-left: 4.5px solid var(--err) !important;
}
.status.warn {
    background: var(--warn_bg) !important;
    color: var(--warn) !important;
    border: 1.5px solid var(--warn) !important;
    border-left: 4.5px solid var(--warn) !important;
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
    background: linear-gradient(135deg, var(--accent) 0%, var(--accent2) 100%) !important;
    color: var(--on_accent) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    padding: 0.65rem 1rem !important;
    margin-top: 0.35rem !important;
    width: 100% !important;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2) !important;
    transition: transform 0.15s ease, filter 0.15s ease !important;
}
.stButton > button[kind="primary"]:hover {
    filter: brightness(1.12) !important;
    transform: translateY(-1px) !important;
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
    background: var(--surface2) !important;
    border: 1.5px solid var(--border) !important;
    color: var(--text) !important;
    font-weight: 700 !important;
    font-size: 0.86rem !important;
    padding: 0.35rem 0.95rem !important;
    box-shadow: 0 2px 4px rgba(15, 23, 42, 0.06) !important;
    transition: all 0.15s ease !important;
}
[data-testid="stButton-pill_explain"] > button:hover,
[data-testid="stButton-pill_debug"] > button:hover,
[data-testid="stButton-pill_optimize"] > button:hover,
[data-testid="stButton-pill_tests"] > button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--surface) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 10px rgba(37, 99, 235, 0.15) !important;
}
/* Explain always highlighted (active) */
[data-testid="stButton-pill_explain"] > button {
    border: 1.5px solid var(--accent) !important;
    color: var(--accent) !important;
    background: rgba(37, 99, 235, 0.08) !important;
    font-weight: 700 !important;
}

/* Seamless Chat Message Container (remove awkward inner gray box) */
div[data-testid="stColumn"]:has(.pane-right) div[data-testid="stVerticalBlockBorderWrapper"],
div[data-testid="column"]:has(.pane-right) div[data-testid="stVerticalBlockBorderWrapper"] {
    border: none !important;
    padding: 0 !important;
    background: transparent !important;
}

/* Chat Messages with prominent crisp boundaries */
[data-testid="stChatMessage"] {
    background: var(--surface) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: 12px !important;
    padding: 0.9rem 1.2rem !important;
    margin-bottom: 0.85rem !important;
    max-height: none !important;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06) !important;
}
/* User message bubble */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: rgba(37, 99, 235, 0.05) !important;
    border-color: rgba(37, 99, 235, 0.3) !important;
}
[data-testid="stChatMessage"] p {
    color: var(--text) !important;
    font-size: 0.92rem !important;
    line-height: 1.5 !important;
    margin: 0 !important;
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

/* Metric Cards */
[data-testid="stMetric"] {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    padding: 0.65rem 0.85rem !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03) !important;
}
[data-testid="stMetricLabel"] p {
    font-size: 0.75rem !important;
    font-weight: 700 !important;
    color: var(--muted) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.2rem !important;
    font-weight: 800 !important;
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
}

/* Expanders in body */
[data-testid="stExpander"] details {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    margin-top: 0.4rem !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary {
    color: var(--text) !important;
    font-weight: 700 !important;
    font-size: 0.88rem !important;
    padding: 0.6rem 0.85rem !important;
}
[data-testid="stExpander"] summary:hover {
    color: var(--accent) !important;
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
    border: 1.5px solid var(--border) !important;
    border-radius: 12px !important;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08) !important;
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

/* =========================================================================
   THEME SELECTBOX & DROPDOWN POPOVERS (PURE LIGHT THEME, NEVER BLACK)
   ========================================================================= */
.stSelectbox,
[data-testid="stSelectbox"],
div[data-baseweb="select"],
div[data-baseweb="select"] > div,
div[data-baseweb="select"] > div:hover,
div[data-baseweb="select"] > div:focus {
    background: var(--surface) !important;
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    color: var(--text) !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
}

div[data-baseweb="select"] [role="combobox"],
div[data-baseweb="select"] [data-testid="stMarkdownContainer"],
div[data-baseweb="select"] div,
div[data-baseweb="select"] span,
div[data-baseweb="select"] p {
    background: transparent !important;
    background-color: transparent !important;
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
}

div[data-baseweb="select"] svg {
    fill: var(--text) !important;
    color: var(--text) !important;
}

div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
ul[role="listbox"],
[data-baseweb="menu"] {
    background: var(--surface) !important;
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.06) !important;
    padding: 4px !important;
    z-index: 999999 !important;
}

li[role="option"],
[data-baseweb="menu"] li {
    background: var(--surface) !important;
    background-color: var(--surface) !important;
    color: var(--text) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    padding: 0.5rem 0.85rem !important;
    margin: 2px 0 !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
}

li[role="option"]:hover,
[data-baseweb="menu"] li:hover,
li[role="option"][aria-selected="true"] {
    background: var(--surface2) !important;
    background-color: var(--surface2) !important;
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
    legacy_map = {
        "Clean Studio": "Studio Light",
        "Daylight": "Studio Light",
        "Forest Dev": "Studio Light",
        "Midnight": "Tokyo Night",
    }
    if st.session_state.get("ui_theme") in legacy_map:
        st.session_state.ui_theme = legacy_map[st.session_state["ui_theme"]]

    if "ui_theme" not in st.session_state or st.session_state.ui_theme not in THEMES:
        st.session_state.ui_theme = DEFAULT_THEME

    # Dynamic model resolution: never hardcode any model
    if not model_name:
        model_name = (
            st.session_state.get("last_served_model")
            or st.session_state.get("ollama_live_model_select")
            or st.session_state.get("gemini_live_model_select")
            or os.environ.get("LIVE_MODEL", "qwen2.5-coder:3b")
        )

    title, picker = st.columns([5.3, 1.2], vertical_alignment="center")
    with title:
        prov_chip = f'<span class="app-chip">{provider.capitalize()}</span>' if provider else ''
        st.markdown(
            f'<div class="app-bar"><span class="app-title">C++ Compiler &amp; Debugger</span>'
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
    # Cleanly strip all outer/inner borders from Ace editor via isolated script
    ace_clean_script = f"""
    <script>
    (function() {{
        try {{
            const win = window.parent || window;
            const doc = win.document;
            win.__aceThemeName = "{editor_ace_theme}";
            const cleanFrames = () => {{
                try {{
                    doc.querySelectorAll('iframe[src*="streamlit_ace"], iframe[title*="streamlit_ace"], div[data-testid="stCustomComponentV1"] iframe').forEach(f => {{
                        f.style.setProperty('border', 'none', 'important');
                        f.style.setProperty('outline', 'none', 'important');
                        f.style.setProperty('box-shadow', 'none', 'important');
                        try {{
                            const fdoc = f.contentDocument;
                            if (fdoc && fdoc.head) {{
                                let s = fdoc.getElementById('cd-ace-borderless');
                                if (!s) {{
                                    s = fdoc.createElement('style');
                                    s.id = 'cd-ace-borderless';
                                    fdoc.head.appendChild(s);
                                }}
                                s.textContent = 'html, body, #root, .MuiPaper-root, .MuiBox-root, .ace_editor {{ background: transparent !important; border: none !important; box-shadow: none !important; outline: none !important; margin: 0 !important; }}';
                            }}
                        }} catch (_) {{}}
                    }});
                }} catch (_) {{}}
            }};
            cleanFrames();
            if (!win.__cleanAceTimer) {{
                win.__cleanAceTimer = win.setInterval(cleanFrames, 300);
            }}
        }} catch (_) {{}}
    }})();
    </script>
    """
    components.html(ace_clean_script, height=0, width=0)
    return theme
