"""
security_layer.py - Perimeter WAF, Isolation Forest Anomaly Detection & Rate-Limit/Fail2ban Guard
==================================================================================================
Implements the multi-layer defensive pipeline in pure Python + NumPy:
  1. OwaspWafFilter: OWASP CRS-inspired HTTP/payload & stdin filtering (XSS, SQLi, cmdi, traversal, SSRF)
  2. IsolationForestDetector: Pure-NumPy Isolation Forest behavioral anomaly scorer
  3. RateLimiterFail2ban: Sliding-window rate limiter with progressive Fail2ban-style IP/session jail
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from audit_logger import log_security_event


# ---------------------------------------------------------------------------
# 1. OWASP CRS-Inspired WAF Payload Filter
# ---------------------------------------------------------------------------
_WAF_RULES: list[tuple[str, re.Pattern[str], str, tuple[str, ...]]] = [
    (
        "CRS-941100",
        re.compile(r"(?i)<\s*script\b|javascript\s*:|on(?:error|load|click|mouseover)\s*="),
        "XSS / Script injection payload detected",
        ("stdin", "headers"),
    ),
    (
        "CRS-942100",
        re.compile(r"(?i)(?:\bunion\s+(?:all\s+)?select\b|'\s*or\s+['\d]+\s*=\s*['\d]+|\bdrop\s+table\b|\bxp_cmdshell\b)"),
        "SQL injection signature detected",
        ("stdin", "headers"),
    ),
    (
        "CRS-932100",
        re.compile(r"(?:;\s*(?:rm\s+-rf|cat\s+/etc|wget\s+|curl\s+)|&&\s*(?:curl|wget|bash|sh|powershell)\b|\|\s*nc\b|`[^`]+`|\$\([^)]+\))"),
        "OS command injection pattern in input/headers",
        ("stdin", "headers"),
    ),
    (
        "CRS-930100",
        re.compile(r"(?i)(?:\.\./\.\./|\.\.\\\.\.\\|%2e%2e%2f|%2e%2e%5c|/etc/passwd|/etc/shadow|c:\\windows\\system32)"),
        "Path traversal / sensitive system file reference",
        ("stdin", "headers", "code"),
    ),
    (
        "CRS-931130",
        re.compile(r"(?i)(?:169\.254\.169\.254|metadata\.google\.internal|\b(?:file|gopher|dict)://)"),
        "SSRF / internal metadata URI detected",
        ("stdin", "headers", "code"),
    ),
]


class OwaspWafFilter:
    """OWASP CRS-style payload and request filter."""

    def inspect(
        self,
        code: str = "",
        stdin_input: str = "",
        headers: dict[str, str] | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Return (allowed, rule_id, reason)."""
        header_blob = "\n".join(f"{k}: {v}" for k, v in (headers or {}).items())
        targets = {
            "stdin": stdin_input or "",
            "headers": header_blob,
            "code": code or "",
        }
        for rule_id, pattern, description, scopes in _WAF_RULES:
            for scope in scopes:
                text = targets.get(scope, "")
                if text and pattern.search(text):
                    return False, rule_id, f"[{rule_id}] {description} (in {scope})"
        return True, None, None


# ---------------------------------------------------------------------------
# 2. Pure-NumPy Isolation Forest Behavioral Anomaly Detector
# ---------------------------------------------------------------------------
def _c_factor(n: int) -> float:
    """Average path length of unsuccessful search in a Binary Search Tree."""
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1.0) + 0.5772156649) - (2.0 * (n - 1.0) / n)


@dataclass
class _IsoNode:
    split_attr: int = -1
    split_val: float = 0.0
    left: "_IsoNode | None" = None
    right: "_IsoNode | None" = None
    size: int = 0


def _build_iso_tree(X: np.ndarray, depth: int, max_depth: int, rng: np.random.Generator) -> _IsoNode:
    n_samples, n_features = X.shape
    if depth >= max_depth or n_samples <= 1:
        return _IsoNode(size=n_samples)

    attr = int(rng.integers(0, n_features))
    col = X[:, attr]
    min_v = float(np.min(col))
    max_v = float(np.max(col))
    if max_v <= min_v:
        return _IsoNode(size=n_samples)

    split_val = float(rng.uniform(min_v, max_v))
    left_mask = col < split_val
    if not np.any(left_mask) or np.all(left_mask):
        return _IsoNode(size=n_samples)

    return _IsoNode(
        split_attr=attr,
        split_val=split_val,
        left=_build_iso_tree(X[left_mask], depth + 1, max_depth, rng),
        right=_build_iso_tree(X[~left_mask], depth + 1, max_depth, rng),
        size=n_samples,
    )


def _path_length(x: np.ndarray, node: _IsoNode, depth: int) -> float:
    if node.left is None or node.right is None:
        return float(depth) + _c_factor(node.size)
    if x[node.split_attr] < node.split_val:
        return _path_length(x, node.left, depth + 1)
    return _path_length(x, node.right, depth + 1)


_SUSPICIOUS_TOKENS_RE = re.compile(
    r"(?i)(?:\\x[0-9a-f]{2}|0x[0-9a-f]{4,}|base64|chmod\s+\+x|wget\s|curl\s|/bin/sh|/bin/bash|powershell)"
)


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def extract_behavior_features(
    code: str,
    stdin_input: str = "",
    recent_rate: float = 0.0,
) -> np.ndarray:
    """Extract 7-D behavioral feature vector for Isolation Forest scoring."""
    code_str = code or ""
    code_len = max(len(code_str), 1)
    entropy = _shannon_entropy(code_str)
    special_chars = sum(1 for ch in code_str if not ch.isalnum() and not ch.isspace())
    special_ratio = special_chars / code_len
    lines = code_str.splitlines() or [""]
    max_line_len = min(max(len(ln) for ln in lines), 2000) / 200.0
    suspicious_hits = len(_SUSPICIOUS_TOKENS_RE.findall(code_str))
    suspicious_density = (suspicious_hits * 100.0) / code_len
    stdin_log_len = math.log1p(len(stdin_input or ""))

    return np.array(
        [
            math.log1p(len(code_str)),
            entropy,
            special_ratio,
            max_line_len,
            suspicious_density,
            stdin_log_len,
            float(recent_rate),
        ],
        dtype=np.float64,
    )


class IsolationForestDetector:
    """Pure-NumPy Isolation Forest trained on baseline normal code submissions."""

    def __init__(
        self,
        n_estimators: int = 100,
        max_samples: int = 64,
        anomaly_threshold: float = 0.58,
        seed: int = 42,
    ) -> None:
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.anomaly_threshold = anomaly_threshold
        self._rng = np.random.default_rng(seed)
        self._trees: list[_IsoNode] = []
        self._fit_baseline()

    def _fit_baseline(self) -> None:
        # Synthetic baseline representing normal student/dev C++, Python, JS, SQL submissions
        n_base = 240
        X = np.column_stack(
            [
                self._rng.uniform(3.2, 8.2, n_base),   # log1p(code_len): ~25 to ~3600 chars
                self._rng.uniform(3.5, 5.2, n_base),   # normal source code Shannon entropy
                self._rng.uniform(0.08, 0.30, n_base), # normal punctuation/operator ratio
                self._rng.uniform(0.08, 0.65, n_base), # max_line_len / 200 (16..130 chars)
                self._rng.uniform(0.0, 0.25, n_base),  # suspicious token density
                self._rng.uniform(0.0, 5.0, n_base),   # log1p(stdin_len)
                self._rng.uniform(0.0, 4.0, n_base),   # normal submissions per 10s
            ]
        )
        max_depth = int(math.ceil(math.log2(self.max_samples)))
        self._trees = []
        for _ in range(self.n_estimators):
            idx = self._rng.choice(n_base, size=min(self.max_samples, n_base), replace=False)
            self._trees.append(_build_iso_tree(X[idx], 0, max_depth, self._rng))

    def score(self, code: str, stdin_input: str = "", recent_rate: float = 0.0) -> float:
        """Return anomaly score in [0, 1]; higher means more anomalous."""
        vec = extract_behavior_features(code, stdin_input=stdin_input, recent_rate=recent_rate)
        avg_path = float(np.mean([_path_length(vec, t, 0) for t in self._trees]))
        c_n = _c_factor(self.max_samples)
        if c_n <= 0:
            return 0.0
        return float(2.0 ** (-avg_path / c_n))

    def inspect(
        self,
        code: str,
        stdin_input: str = "",
        recent_rate: float = 0.0,
    ) -> tuple[bool, float, str | None]:
        """Return (allowed, anomaly_score, reason_if_blocked)."""
        s = self.score(code, stdin_input=stdin_input, recent_rate=recent_rate)
        if s >= self.anomaly_threshold:
            return (
                False,
                round(s, 4),
                f"IsolationForest anomaly score {s:.2f} >= {self.anomaly_threshold:.2f} (abnormal payload/behavior)",
            )
        return True, round(s, 4), None


# ---------------------------------------------------------------------------
# 3. Sliding-Window Rate Limiter + Fail2ban Jail
# ---------------------------------------------------------------------------
class RateLimiterFail2ban:
    """Thread-safe sliding-window rate limiter with progressive Fail2ban IP/session jail."""

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        ban_threshold: int = 5,
        strike_window: float = 300.0,
        ban_duration: float = 300.0,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.ban_threshold = ban_threshold
        self.strike_window = strike_window
        self.ban_duration = ban_duration
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._strikes: dict[str, deque[float]] = defaultdict(deque)
        self._banned_until: dict[str, float] = {}

    def _prune(self, dq: deque[float], cutoff: float) -> None:
        while dq and dq[0] < cutoff:
            dq.popleft()

    def recent_burst_count(self, client_id: str, burst_window: float = 10.0) -> int:
        now = time.time()
        with self._lock:
            dq = self._requests.get(client_id)
            if not dq:
                return 0
            cutoff = now - burst_window
            return sum(1 for ts in dq if ts >= cutoff)

    def check_and_consume(self, client_id: str) -> tuple[bool, int, float | None, str | None]:
        """Return (allowed, remaining_quota, banned_until_ts, reason)."""
        now = time.time()
        with self._lock:
            ban_exp = self._banned_until.get(client_id)
            if ban_exp is not None:
                if now < ban_exp:
                    remaining_ban = int(math.ceil(ban_exp - now))
                    return False, 0, ban_exp, f"Fail2ban active: client '{client_id}' banned for {remaining_ban}s"
                del self._banned_until[client_id]

            dq = self._requests[client_id]
            self._prune(dq, now - self.window_seconds)
            if len(dq) >= self.max_requests:
                ban_exp = self._record_strike_locked(client_id, now, reason="Rate limit exceeded")
                msg = (
                    f"Fail2ban triggered: client '{client_id}' banned for {int(self.ban_duration)}s"
                    if ban_exp
                    else f"Rate limit exceeded ({self.max_requests} req / {int(self.window_seconds)}s)"
                )
                return False, 0, ban_exp, msg

            dq.append(now)
            remaining = max(0, self.max_requests - len(dq))
            return True, remaining, None, None

    def record_strike(self, client_id: str, reason: str) -> float | None:
        """Record a security violation strike; returns ban_until timestamp if newly banned."""
        with self._lock:
            return self._record_strike_locked(client_id, time.time(), reason=reason)

    def _record_strike_locked(self, client_id: str, now: float, reason: str) -> float | None:
        sdq = self._strikes[client_id]
        self._prune(sdq, now - self.strike_window)
        sdq.append(now)
        if len(sdq) >= self.ban_threshold:
            ban_exp = now + self.ban_duration
            self._banned_until[client_id] = ban_exp
            sdq.clear()
            _emit_fail2ban_log(client_id, reason, int(self.ban_duration))
            return ban_exp
        return None


def _emit_fail2ban_log(client_id: str, reason: str, duration_s: int) -> None:
    """Write a Fail2ban-compatible ban line to security_audit.log."""
    audit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "security_audit.log")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [TOACD-BAN] client={client_id} reason={reason} duration={duration_s}s\n"
    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 4. Unified Perimeter Security Evaluator
# ---------------------------------------------------------------------------
@dataclass
class SecurityLayerResult:
    allowed: bool
    blocked_by: str | None = None
    reason: str | None = None
    anomaly_score: float = 0.0
    waf_rule_id: str | None = None
    rate_remaining: int = 0
    banned_until: float | None = None


_DEFAULT_WAF = OwaspWafFilter()
_DEFAULT_IFOREST = IsolationForestDetector()
_DEFAULT_RATE_LIMITER = RateLimiterFail2ban()


def evaluate_request_security(
    code: str,
    stdin_input: str = "",
    client_id: str = "local-session",
    headers: dict[str, str] | None = None,
    waf: OwaspWafFilter | None = None,
    iforest: IsolationForestDetector | None = None,
    rate_limiter: RateLimiterFail2ban | None = None,
) -> SecurityLayerResult:
    """Run Perimeter Security Layer: Fail2ban/RateLimit -> OWASP WAF -> Isolation Forest."""
    waf_filter = waf or _DEFAULT_WAF
    iso_detector = iforest or _DEFAULT_IFOREST
    limiter = rate_limiter or _DEFAULT_RATE_LIMITER

    # Step 1: Fail2ban Jail + Sliding-Window Rate Limit
    rl_ok, remaining, ban_exp, rl_reason = limiter.check_and_consume(client_id)
    if not rl_ok:
        log_security_event(code[:200], rl_reason or "Rate limit / Fail2ban", user_action="Perimeter RateLimit/Fail2ban")
        return SecurityLayerResult(
            allowed=False,
            blocked_by="Fail2ban" if ban_exp else "RateLimiter",
            reason=rl_reason,
            rate_remaining=0,
            banned_until=ban_exp,
        )

    # Step 2: OWASP CRS / WAF Payload Inspection
    waf_ok, rule_id, waf_reason = waf_filter.inspect(code=code, stdin_input=stdin_input, headers=headers)
    if not waf_ok:
        ban_exp = limiter.record_strike(client_id, waf_reason or "WAF Violation")
        log_security_event(code[:200], waf_reason or "WAF Block", user_action="OWASP WAF Filter")
        return SecurityLayerResult(
            allowed=False,
            blocked_by="ModSecurity/OWASP-WAF",
            reason=waf_reason,
            waf_rule_id=rule_id,
            rate_remaining=remaining,
            banned_until=ban_exp,
        )

    # Step 3: Isolation Forest Behavioral Anomaly Detection
    burst_rate = float(limiter.recent_burst_count(client_id))
    iso_ok, anomaly_score, iso_reason = iso_detector.inspect(
        code=code,
        stdin_input=stdin_input,
        recent_rate=burst_rate,
    )
    if not iso_ok:
        ban_exp = limiter.record_strike(client_id, iso_reason or "IsolationForest Anomaly")
        log_security_event(code[:200], iso_reason or "Anomaly Block", user_action="IsolationForest Anomaly Guard")
        return SecurityLayerResult(
            allowed=False,
            blocked_by="IsolationForest",
            reason=iso_reason,
            anomaly_score=anomaly_score,
            rate_remaining=remaining,
            banned_until=ban_exp,
        )

    return SecurityLayerResult(
        allowed=True,
        anomaly_score=anomaly_score,
        rate_remaining=remaining,
    )
