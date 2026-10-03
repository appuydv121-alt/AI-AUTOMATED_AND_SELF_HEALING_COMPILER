"""Data models for the reasoning pipeline.

Every inter-component value is one of these types.  No component other than
``llm_client`` performs HTTP; no component other than ``compiler_facts`` /
``verify`` spawns processes.  All LLM I/O is JSON validated against schemas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Tier enum (ordered by severity, highest first) ──────────────────────────

class Tier(Enum):
    """Certainty tier — the LLM proposes, ``tier_policy`` caps."""
    DEFINITE_ERROR = "definite_error"
    DEFINITE_BUG   = "definite_bug"
    LIKELY_BUG     = "likely_bug"
    POSSIBLE_ISSUE = "possible_issue"
    CODE_SMELL     = "code_smell"
    OPTIMIZATION   = "optimization"
    STYLE          = "style"


TIER_SEVERITY_ORDER = {t: i for i, t in enumerate(Tier)}


# ── Facts ────────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    """A single, deterministic, line-anchored piece of evidence."""
    id: str
    source: str          # "compiler", "ast", "dataflow", "hint"
    kind: str            # e.g. "warning", "note", "decl", "read", "write", "may_read_unassigned"
    line: Optional[int]
    message: str
    option: str = ""     # e.g. "-Wuninitialized"
    children: list["Fact"] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        """Human-readable single-line representation for prompt injection."""
        parts = [f"{self.id} {self.source}"]
        if self.option:
            parts.append(f"[{self.option}]")
        if self.line is not None:
            parts.append(f"@L{self.line}")
        parts.append(f'"{self.message}"')
        for child in self.children:
            parts.append(f"; {child.kind} @L{child.line} \"{child.message}\"")
        return " ".join(parts)


@dataclass
class FactBundle:
    """All deterministic facts about a source file, ready for prompt injection."""
    facts: list[Fact] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)
    functions: list[dict[str, Any]] = field(default_factory=list)
    scopes: list[dict[str, Any]] = field(default_factory=list)
    declarations: list[dict[str, Any]] = field(default_factory=list)
    dataflow_events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    frontend_used: str = "none"

    def fact_text_block(self) -> str:
        """All facts as a numbered block for prompts."""
        return "\n".join(f.text() for f in self.facts)

    def get_function_slice(self, func_name: str) -> Optional[str]:
        """Return the source lines for a named function, or None."""
        for fn in self.functions:
            if fn.get("name") == func_name:
                start = fn.get("start_line", 1) - 1
                end = fn.get("end_line", len(self.source_lines))
                return "\n".join(self.source_lines[start:end])
        return None


# ── Intent model (Stage 1 output) ──────────────────────────────────────────

@dataclass
class VariableRole:
    name: str
    role: str
    confidence: float
    evidence_lines: list[int] = field(default_factory=list)


@dataclass
class IntentModel:
    purpose: str = ""
    variable_roles: list[VariableRole] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


# ── Candidate (Stage 2 output) ─────────────────────────────────────────────

@dataclass
class Candidate:
    claim: str
    kind: str                          # free-text label
    lines: list[int] = field(default_factory=list)
    snippet: str = ""
    fact_ids: list[str] = field(default_factory=list)
    depends_on_intent: bool = False
    proposed_tier: str = "possible_issue"
    why_this_may_be_fine: str = ""
    falsifier: str = ""
    # filled later
    gate_status: str = ""              # "pass" | "reject" | "downgrade"
    gate_reason: str = ""
    refute_verdict: str = ""           # "holds" | "refuted" | "uncertain"
    refute_reason: str = ""


# ── Patch ───────────────────────────────────────────────────────────────────

@dataclass
class PatchEdit:
    start_line: int
    end_line: int
    replacement: str


# ── Verification ────────────────────────────────────────────────────────────

@dataclass
class VerificationReport:
    """Results of the verification ladder (V0–V2 structural only)."""
    v0_applies: str = "skipped"        # "passed" | "failed" | "skipped"
    v1_scope: str = "skipped"
    v2_security: str = "skipped"
    v0_detail: str = ""
    v1_detail: str = ""
    v2_detail: str = ""

    @property
    def all_passed(self) -> bool:
        return all(
            getattr(self, f"v{i}_{name}") in ("passed", "skipped")
            for i, name in enumerate(["applies", "scope", "security"])
        )

    @property
    def summary(self) -> str:
        parts = []
        labels = {0: "patch applies", 1: "scope check", 2: "security scan"}
        for i, name in enumerate(["applies", "scope", "security"]):
            status = getattr(self, f"v{i}_{name}")
            sym = "✓" if status == "passed" else ("—" if status == "skipped" else "✗")
            parts.append(f"{labels[i]} {sym}")
        return " · ".join(parts)


# ── Finding (final output of the pipeline) ──────────────────────────────────

@dataclass
class Finding:
    id: str = field(default_factory=lambda: f"R-{uuid.uuid4().hex[:6]}")
    tier: Tier = Tier.POSSIBLE_ISSUE
    kind: str = ""
    lines: list[int] = field(default_factory=list)
    title: str = ""
    explanation: str = ""
    reasoning: str = ""
    confidence: float = 0.0
    depends_on_intent: bool = False
    intent_note: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)
    why_this_may_be_fine: str = ""
    edge_cases: list[str] = field(default_factory=list)
    proposed_patch: Optional[dict[str, Any]] = None
    verification: Optional[VerificationReport] = None
    optimization: Optional[dict[str, Any]] = None
    source: str = "reasoning"
    fact_ids: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)


# ── Stage result wrapper ────────────────────────────────────────────────────

@dataclass
class StageResult:
    status: str = "ok"                 # "ok" | "skipped" | "failed" | "timeout"
    data: Any = None
    error: str = ""


# ── Optimization analysis (Stage 8) ────────────────────────────────────────

@dataclass
class OptimizationAnalysis:
    specification: str = ""
    current_complexity: str = ""
    is_worth_changing: str = ""
    alternative: str = ""
    alternative_complexity: str = ""
    tradeoffs: list[str] = field(default_factory=list)
    recommendation: str = "leave_as_is"  # "change" | "optional" | "leave_as_is"


# ── Top-level result returned by run_reasoning() ───────────────────────────

@dataclass
class ReasoningResult:
    findings: list[Finding] = field(default_factory=list)
    fact_bundle: Optional[FactBundle] = None
    intent_model: Optional[IntentModel] = None
    stages_run: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    profile: str = "standard"
    elapsed_seconds: float = 0.0
    partial: bool = False              # True if budget was exhausted early
