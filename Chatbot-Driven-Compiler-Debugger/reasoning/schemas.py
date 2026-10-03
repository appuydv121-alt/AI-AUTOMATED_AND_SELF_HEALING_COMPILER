"""JSON Schemas for structured LLM output at each pipeline stage.

Each schema is used as the ``format`` parameter in the Ollama API call,
which constrains output at the sampling level.
"""

# ── Stage 1: Comprehend ─────────────────────────────────────────────────────

COMPREHEND_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string", "description": "What this code appears to do"},
        "variable_roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence_lines": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["name", "role", "confidence"],
            },
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["purpose", "variable_roles", "unknowns"],
}

# ── Stage 2: Hypothesize ────────────────────────────────────────────────────

HYPOTHESIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "kind": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "snippet": {"type": "string"},
                    "fact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "depends_on_intent": {"type": "boolean"},
                    "proposed_tier": {"type": "string"},
                    "why_this_may_be_fine": {"type": "string"},
                    "falsifier": {"type": "string"},
                },
                "required": ["claim", "kind", "lines", "snippet", "fact_ids",
                             "proposed_tier", "why_this_may_be_fine"],
            },
        },
    },
    "required": ["candidates"],
}

# ── Stage 4: Refute ─────────────────────────────────────────────────────────

REFUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "strongest_counter": {
            "type": "string",
            "description": "The strongest reading under which the code is correct",
        },
        "verdict": {
            "type": "string",
            "description": "holds, refuted, or uncertain",
        },
        "reason": {"type": "string"},
        "conditions_for_correctness": {"type": "string"},
    },
    "required": ["strongest_counter", "verdict", "reason"],
}

# ── Stage 6: Fix ────────────────────────────────────────────────────────────

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "can_fix": {"type": "boolean"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "replacement": {"type": "string"},
                },
                "required": ["start_line", "end_line", "replacement"],
            },
        },
        "rationale": {"type": "string"},
        "alternatives": {"type": "string"},
    },
    "required": ["can_fix", "edits", "rationale"],
}

# ── Stage 8: Optimize ──────────────────────────────────────────────────────

OPTIMIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "specification": {"type": "string"},
        "current_complexity": {"type": "string"},
        "is_worth_changing": {"type": "string"},
        "alternative": {"type": "string"},
        "alternative_complexity": {"type": "string"},
        "tradeoffs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommendation": {"type": "string"},
    },
    "required": ["specification", "current_complexity", "is_worth_changing",
                 "alternative", "recommendation"],
}

# ── Stage 9: Explain ───────────────────────────────────────────────────────

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["title", "explanation"],
}
