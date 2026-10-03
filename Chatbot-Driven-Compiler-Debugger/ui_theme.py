"""Shared presentation helpers for Streamlit themes and status markup."""

from functools import lru_cache
import html
import os


_ASSET_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_ACE_THEME_SCRIPT = """
(function () {
    if (window.__cppDebuggerAceThemeObserver) return;

    const syncAceTheme = () => {
        const app = document.querySelector('[data-testid="stApp"]');
        if (!app) return;
        const scheme = getComputedStyle(app).colorScheme;
        const themeName = scheme === "dark" ? "ace/theme/tomorrow_night" : "ace/theme/chrome";
        document.querySelectorAll('iframe[src*="streamlit_ace"]').forEach((frame) => {
            try {
                const editorElement = frame.contentDocument.querySelector(".ace_editor");
                const ace = frame.contentWindow.ace;
                if (!editorElement || !ace) return;
                const editor = ace.edit(editorElement);
                if (editor.getTheme() !== themeName) editor.setTheme(themeName);
            } catch (_) {
            }
        });
    };

    const observer = new MutationObserver(syncAceTheme);
    observer.observe(document.body, {
        attributes: true,
        childList: true,
        subtree: true,
        attributeFilter: ["class", "style"],
    });
    window.__cppDebuggerAceThemeObserver = observer;
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
            f"<style>{stylesheet}</style><script>{_ACE_THEME_SCRIPT}</script>",
            unsafe_allow_javascript=True,
        )
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
