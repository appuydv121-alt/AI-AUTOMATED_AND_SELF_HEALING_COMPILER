"""Environment-driven configuration for the reasoning pipeline."""

import os

# ── Ollama ──────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_TAGS_URL = os.environ.get("OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# ── Feature flags ───────────────────────────────────────────────────────────
REASONING_ENABLED = os.environ.get("REASONING_ENABLED", "true").lower() not in {"0", "false", "no"}

# ── Profile: "quick", "standard", "thorough" ───────────────────────────────
DEFAULT_PROFILE = os.environ.get("REASONING_PROFILE", "standard")

# ── Per-stage timeout in seconds (wall-clock for a single LLM call) ────────
STAGE_TIMEOUT = {
    "comprehend":  int(os.environ.get("REASONING_TIMEOUT_COMPREHEND",  "12")),
    "hypothesize": int(os.environ.get("REASONING_TIMEOUT_HYPOTHESIZE", "12")),
    "refute":      int(os.environ.get("REASONING_TIMEOUT_REFUTE",      "10")),
    "fix":         int(os.environ.get("REASONING_TIMEOUT_FIX",         "12")),
    "optimize":    int(os.environ.get("REASONING_TIMEOUT_OPTIMIZE",    "12")),
    "explain":     int(os.environ.get("REASONING_TIMEOUT_EXPLAIN",     "8")),
}

# Global budget for the entire pipeline (seconds)
GLOBAL_BUDGET = int(os.environ.get("REASONING_GLOBAL_BUDGET", "30"))

# ── LLM request options ────────────────────────────────────────────────────
LLM_TEMPERATURE = float(os.environ.get("REASONING_TEMPERATURE", "0"))
LLM_SEED = int(os.environ.get("REASONING_SEED", "42"))

# ── Compiler flags for the warnings compile ─────────────────────────────────
WARNINGS_FLAGS = ["-Wall", "-Wextra", "-O1", "-std=c++17"]
WARNINGS_JSON_FORMAT = True  # use -fdiagnostics-format=json (GCC >= 9)

# ── Profiles define which stages run ────────────────────────────────────────
PROFILE_STAGES = {
    "quick":    {"comprehend", "hypothesize", "evidence_gate", "tier", "explain"},
    "standard": {"comprehend", "hypothesize", "evidence_gate", "refute", "tier", "fix", "verify", "explain"},
    "thorough": {"comprehend", "hypothesize", "evidence_gate", "refute", "tier", "fix", "verify", "optimize", "explain"},
}
