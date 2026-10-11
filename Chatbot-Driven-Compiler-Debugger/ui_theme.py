"""Shared presentation helpers for Streamlit themes and status markup."""

from functools import lru_cache
import html
import os


import streamlit.components.v1 as components

_ASSET_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_ACE_THEME_SCRIPT = """
(function () {
    const win = (typeof window !== "undefined" && window.parent) ? window.parent : window;
    const doc = win.document;

    win.__currentAceAnnotations = win.__currentAceAnnotations || [];
    win.__setAceAnnotations = function(annotations) {
        win.__currentAceAnnotations = annotations;
        doc.querySelectorAll('iframe[src*="streamlit_ace"]').forEach(function(frame) {
            try {
                const editorElement = frame.contentDocument && frame.contentDocument.querySelector(".ace_editor");
                const ace = frame.contentWindow && frame.contentWindow.ace;
                if (!editorElement || !ace) return;
                const editor = ace.edit(editorElement);
                editor.getSession().setAnnotations(annotations);
            } catch (_) {}
        });
    };

    function findColumnBlock() {
        const anchor = doc.querySelector('.ui-editor-panel');
        if (anchor) {
            const b = anchor.closest('[data-testid="stHorizontalBlock"]');
            if (b) return b;
        }
        const blocks = doc.querySelectorAll('[data-testid="stHorizontalBlock"]');
        for (let i = 0; i < blocks.length; i++) {
            const b = blocks[i];
            const cols = b.querySelectorAll(':scope > [data-testid="stColumn"]');
            if (cols.length === 2) {
                return b;
            }
        }
        return null;
    }

    let isDragging = false;
    let currentSplitPct = parseFloat(win.localStorage.getItem('leetcode_split_ratio') || '50');
    if (isNaN(currentSplitPct) || currentSplitPct < 15 || currentSplitPct > 85) currentSplitPct = 50;

    function applySplit(leftPct) {
        currentSplitPct = Math.max(15, Math.min(85, leftPct));
        try { win.localStorage.setItem('leetcode_split_ratio', currentSplitPct); } catch (_) {}
        const container = findColumnBlock();
        if (!container) return;
        const cols = container.querySelectorAll(':scope > [data-testid="stColumn"]');
        if (cols.length < 2) return;
        
        const colLeft = cols[0];
        const colRight = cols[1];
        const leftWidth = `calc(${currentSplitPct}% - 7px)`;
        const rightWidth = `calc(${100 - currentSplitPct}% - 7px)`;
        
        colLeft.style.setProperty('flex', `0 0 ${leftWidth}`, 'important');
        colLeft.style.setProperty('max-width', leftWidth, 'important');
        colLeft.style.setProperty('width', leftWidth, 'important');
        
        colRight.style.setProperty('flex', `0 0 ${rightWidth}`, 'important');
        colRight.style.setProperty('max-width', rightWidth, 'important');
        colRight.style.setProperty('width', rightWidth, 'important');

        doc.querySelectorAll('iframe[src*="streamlit_ace"]').forEach(function(frame) {
            try {
                const el = frame.contentDocument && frame.contentDocument.querySelector(".ace_editor");
                const ace = frame.contentWindow && frame.contentWindow.ace;
                if (el && ace) {
                    ace.edit(el).resize();
                }
            } catch (_) {}
        });
    }

    win.__initLeetCodeSplitter = function() {
        const container = findColumnBlock();
        if (!container) return;
        const cols = container.querySelectorAll(':scope > [data-testid="stColumn"]');
        if (cols.length < 2) return;

        let gutter = container.querySelector('.leetcode-split-gutter');
        if (!gutter) {
            gutter = doc.createElement('div');
            gutter.className = 'leetcode-split-gutter';
            gutter.innerHTML = '<div class="leetcode-split-line"><div class="leetcode-split-handle"></div></div>';
            gutter.title = 'Drag horizontally to resize panels (Double-click to reset 50:50)';
            
            cols[0].after(gutter);

            gutter.addEventListener('mousedown', function(e) {
                isDragging = true;
                doc.body.classList.add('is-resizing-columns');
                e.preventDefault();
            });

            gutter.addEventListener('dblclick', function(e) {
                applySplit(50);
            });
        }

        applySplit(currentSplitPct);
    };

    if (!win.__leetcodeSplitterMouseRegistered) {
        win.__leetcodeSplitterMouseRegistered = true;
        win.addEventListener('mousemove', function(e) {
            if (!isDragging) return;
            const container = findColumnBlock();
            if (!container) return;
            const rect = container.getBoundingClientRect();
            if (rect.width <= 0) return;
            const offset = e.clientX - rect.left;
            const newPct = (offset / rect.width) * 100;
            applySplit(newPct);
        });

        win.addEventListener('mouseup', function() {
            if (isDragging) {
                isDragging = false;
                doc.body.classList.remove('is-resizing-columns');
            }
        });
    }

    const syncAceTheme = () => {
        const app = doc.querySelector('[data-testid="stApp"]');
        if (!app) return;
        const scheme = win.getComputedStyle(app).colorScheme;
        const themeName = scheme === "dark" ? "ace/theme/tomorrow_night" : "ace/theme/chrome";
        doc.querySelectorAll('iframe[src*="streamlit_ace"]').forEach((frame) => {
            try {
                const editorElement = frame.contentDocument && frame.contentDocument.querySelector(".ace_editor");
                const ace = frame.contentWindow && frame.contentWindow.ace;
                if (!editorElement || !ace) return;
                const editor = ace.edit(editorElement);
                if (editor.getTheme() !== themeName) editor.setTheme(themeName);
                if (win.__currentAceAnnotations && win.__currentAceAnnotations.length) {
                    editor.getSession().setAnnotations(win.__currentAceAnnotations);
                }
            } catch (_) {}
        });
        if (win.__initLeetCodeSplitter) win.__initLeetCodeSplitter();
    };

    if (!win.__leetcodeInterval) {
        win.__leetcodeInterval = win.setInterval(syncAceTheme, 350);
    }
    syncAceTheme();
})();
"""


@lru_cache(maxsize=1)
def _load_stylesheet() -> str:
    stylesheet_path = os.path.join(_ASSET_DIRECTORY, "app.css")
    try:
        with open(stylesheet_path, "r", encoding="utf-8") as stylesheet_file:
            return stylesheet_file.read()
    except OSError:
        return ""


def active_theme(streamlit) -> str:
    """Return the current Streamlit theme type or `system` as a CSS fallback."""
    try:
        theme_type = streamlit.context.theme.type
    except (AttributeError, RuntimeError):
        theme_type = None
    return theme_type if theme_type in {"light", "dark"} else "system"


def ace_theme(theme_type: str) -> str:
    return "tomorrow_night" if theme_type == "dark" else "chrome"


def inject_theme(streamlit) -> str:
    theme_type = active_theme(streamlit)
    stylesheet = _load_stylesheet()
    if stylesheet:
        streamlit.html(
            f"<style>{stylesheet}</style><img src=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E\" onload=\"if(window.__initLeetCodeSplitter) window.__initLeetCodeSplitter();\" style=\"display:none;\">"
        )
        components.html(f"<script>{_ACE_THEME_SCRIPT}</script>", height=0, width=0)
    return theme_type


def badge(label: str, *, tone: str = "info") -> str:
    safe_tone = tone if tone in {"error", "warning", "success", "info", "suggestion"} else "info"
    return f'<span class="ui-chip ui-status-{safe_tone}">{html.escape(label)}</span>'


def code_label(label: str) -> str:
    return f'<span class="ui-code-label">{html.escape(label)}</span>'


def severity_block(
    level: str,
    label: str,
    message: str,
    *,
    icon: str = "",
    line: str | None = None,
    details: tuple[tuple[str, str], ...] = (),
    confidence: str | None = None,
) -> str:
    safe_level = level.lower()
    if safe_level not in {"error", "warning", "success", "info", "suggestion"}:
        safe_level = "info"
    role = "alert" if safe_level == "error" else "status"
    safe_icon = f'<span class="ui-status-icon" aria-hidden="true">{html.escape(icon)}</span>' if icon else ""
    metadata = []
    if line:
        metadata.append(f'<span class="ui-status-line">{html.escape(line)}</span>')
    if confidence:
        metadata.append(f'<span class="ui-status-confidence">{html.escape(confidence)}</span>')
    heading_metadata = "".join(metadata)
    detail_markup = "".join(
        '<div class="ui-semantic-key">'
        + html.escape(key)
        + '</div><div class="ui-semantic-value">'
        + html.escape(value)
        + "</div>"
        for key, value in details
    )
    details_markup = f'<div class="ui-semantic-fields">{detail_markup}</div>' if detail_markup else ""
    return (
        f'<div class="ui-status ui-status-{safe_level}" role="{role}">'
        f'{safe_icon}<div class="ui-status-content">'
        f'<div class="ui-status-heading">{html.escape(label)}{heading_metadata}</div>'
        f'<div class="ui-status-message">{html.escape(message)}</div>'
        f'{details_markup}</div></div>'
    )


def show_status(streamlit, level: str, label: str, message: str, *, icon: str = "") -> None:
    streamlit.markdown(
        severity_block(level, label, message, icon=icon),
        unsafe_allow_html=True,
    )
