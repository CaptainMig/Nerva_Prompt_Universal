"""
nerva_gate.py
NERVA v11.1 escalation gate — bounded ESCALATE with unreachable-operator fallback.

Reference implementation of the state machine in the v11.1 spec (section 3).
Integrators port this; it is the behavior DiMOS and any other actuator-side
gate must match. The kernel (nerva_kernel.py) scores; this module decides what
the actuator path is allowed to do while an escalation is unanswered.

Design rules (spec section 2), all enforced here:
  1. The default is stillness, never approval. No path turns an unanswered
     ESCALATE into COMMIT.
  2. ESCALATE is never silently downgraded. Every fallback is recorded.
  3. Waits are bounded. Every hold has a timeout and a retry schedule.
  4. The gate never invents a safe behavior. The integrator supplies one;
     absent one, the gate holds.
  5. TOXIC never releases without an operator.
  7. A retry is a bandwidth spend and is counted as one (comms_events).

The gate is per proposed action: construct one Gate per action episode and
call step() once per scoring tick with the kernel's verdict for that tick.
Time is supplied by the caller in seconds (sim or wall), so the gate is
deterministic and replayable.

Starpoint LLC
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ─────────────────────────────────────────────────────────────────────────────

COMMIT            = "COMMIT"
HOLD              = "HOLD"
WAIT              = "WAIT"
ESCALATE_PENDING  = "ESCALATE_PENDING"
FALLBACK_HOLD     = "FALLBACK_HOLD"
TOXIC_LATCHED     = "TOXIC_LATCHED"
STATES = (COMMIT, HOLD, WAIT, ESCALATE_PENDING, FALLBACK_HOLD, TOXIC_LATCHED)

REASON_UNCERTAINTY    = "uncertainty"
REASON_EMERGENCY      = "emergency_irreversible"
REASON_LOW_CONF       = "low_confidence_foundation"
REASONS = (REASON_UNCERTAINTY, REASON_EMERGENCY, REASON_LOW_CONF)

# Reasons that evidence alone can never release (spec §3). An irreversible
# action that needed a human still needs one.
IRREVERSIBLE_REASONS = frozenset({REASON_EMERGENCY, REASON_LOW_CONF})

RELEASED_BY_OPERATOR = "operator"
RELEASED_BY_EVIDENCE = "evidence"
RELEASED_BY_NONE     = "none"

OPERATOR_COMMIT  = "commit"
OPERATOR_WAIT    = "wait"
OPERATOR_RELEASE = "release"   # TOXIC_LATCHED only

# Kernel verdicts that mean "ask a human". The kit kernel says CONSULT; the
# v11 kernel says ESCALATE. Both enter ESCALATE_PENDING.
DEFAULT_ESCALATION_VERDICTS = frozenset({"ESCALATE", "CONSULT"})


# ─────────────────────────────────────────────────────────────────────────────
# Parameters (spec §8: provisional, hand-set, to be calibrated)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GateParams:
    T_esc:          float = 5.0    # s, first escalation timeout
    N_retry:        int   = 3      # retries before fallback
    backoff_factor: float = 2.0    # each retry waits backoff_factor × longer
    delta:          float = 0.08   # release margin above the bar (v11 value)
    k:              int   = 20     # consecutive ticks above bar + delta (2.0 s at 10 Hz)

    def total_bounded_wait(self) -> float:
        """Sim seconds from escalation to FALLBACK_HOLD with no reply and the
        link status unknown: T_esc · Σ_{i=0..N_retry} b^i."""
        return self.T_esc * sum(self.backoff_factor ** i for i in range(self.N_retry + 1))


# ─────────────────────────────────────────────────────────────────────────────
# Release rule (the existing v11 rule, reused for FALLBACK_HOLD → COMMIT)
# ─────────────────────────────────────────────────────────────────────────────

class ReleaseRule:
    """Satisfied when signal ≥ bar + delta for k consecutive ticks."""

    def __init__(self, delta: float, k: int):
        self.delta = delta
        self.k = k
        self.dwell = 0

    def reset(self) -> None:
        self.dwell = 0

    def update(self, signal: Optional[float], bar: Optional[float]) -> bool:
        if signal is None or bar is None:
            self.dwell = 0
            return False
        if signal >= bar + self.delta:
            self.dwell += 1
        else:
            self.dwell = 0
        return self.dwell >= self.k


# ─────────────────────────────────────────────────────────────────────────────
# Receipt
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GateReceipt:
    """One line per tick. Spec §5 fields plus the tick context."""
    tick:                   int
    t:                      float
    verdict_in:             str
    state:                  str
    action_allowed:         bool
    # §5
    escalated_at:           Optional[float]
    escalation_reason:      Optional[str]
    operator_reachable:     Optional[bool]
    timeout_s:              Optional[float]
    retries:                int
    backoff_factor:         float
    fallback_invoked:       bool
    safe_action_id:         Optional[str]     # None when no fallback yet; "none" when fallback with no safe_action
    released_by:            str               # operator | evidence | none
    dwell_ticks_at_release: Optional[int]
    comms_events:           int
    receipt_hash:           str = ""

    def finalize(self) -> "GateReceipt":
        payload = asdict(self)
        payload.pop("receipt_hash", None)
        self.receipt_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:12]
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Gate
# ─────────────────────────────────────────────────────────────────────────────

class Gate:
    def __init__(
        self,
        safe_action: Optional[Callable[[], str]] = None,
        params: Optional[GateParams] = None,
        escalation_verdicts: frozenset = DEFAULT_ESCALATION_VERDICTS,
    ):
        self.safe_action = safe_action
        self.p = params or GateParams()
        self.escalation_verdicts = frozenset(escalation_verdicts)
        self.release = ReleaseRule(self.p.delta, self.p.k)

        self.tick = 0
        self.state: Optional[str] = None
        self.operator_reachable: Optional[bool] = None   # None = unknown

        # escalation bookkeeping
        self.escalated_at: Optional[float] = None
        self.escalation_reason: Optional[str] = None
        self.timeout_s: Optional[float] = None
        self.next_retry_at: Optional[float] = None
        self.retries = 0
        self.fallback_invoked = False
        self.safe_action_id: Optional[str] = None
        self.comms_events = 0
        self.receipts: list[GateReceipt] = []

    # ── transitions ─────────────────────────────────────────────────────────

    def _enter_pending(self, t: float, reason: Optional[str], *, fresh: bool) -> None:
        """Enter ESCALATE_PENDING. fresh=True records a new escalation;
        fresh=False resumes the retry schedule from zero after the link returns."""
        if fresh and reason not in REASONS:
            raise ValueError(f"escalation_reason must be one of {REASONS}, got {reason!r}")
        self.state = ESCALATE_PENDING
        if fresh:
            self.escalated_at = t
            self.escalation_reason = reason
            self.fallback_invoked = False
            self.safe_action_id = None
        self.retries = 0
        self.timeout_s = self.p.T_esc
        self.next_retry_at = t + self.timeout_s
        self.comms_events += 1          # the signal itself is a comms event
        self.release.reset()

    def _enter_fallback(self, t: float) -> None:
        self.state = FALLBACK_HOLD
        self.fallback_invoked = True
        self.timeout_s = None
        self.next_retry_at = None
        self.release.reset()
        if self.safe_action is not None:
            self.safe_action_id = str(self.safe_action())
        else:
            self.safe_action_id = "none"

    def _clear_escalation(self) -> None:
        self.escalated_at = None
        self.escalation_reason = None
        self.timeout_s = None
        self.next_retry_at = None
        self.retries = 0
        self.fallback_invoked = False
        self.safe_action_id = None
        self.release.reset()

    # ── step ────────────────────────────────────────────────────────────────

    def step(
        self,
        verdict: str,
        *,
        t: float,
        reason: Optional[str] = None,
        signal: Optional[float] = None,
        bar: Optional[float] = None,
        operator_reachable: Optional[bool] = None,
        operator_response: Optional[str] = None,
    ) -> GateReceipt:
        """Advance one tick. `verdict` is the kernel's verdict for this tick.
        `reason` is required the tick an escalation verdict first arrives.
        `signal`/`bar` are the kernel's |r| (or C) and τ for the evidence rule.
        `operator_reachable` is the link status if known this tick.
        `operator_response` is 'commit' | 'wait' | 'release' when an operator answers."""
        self.tick += 1
        released_by = RELEASED_BY_NONE
        dwell_at_release: Optional[int] = None

        prev_reachable = self.operator_reachable
        if operator_reachable is not None:
            self.operator_reachable = operator_reachable
        link_returned = prev_reachable is False and self.operator_reachable is True

        # ── TOXIC: any → latched; operator-only release; no timeout, no fallback
        if verdict == "TOXIC" and self.state != TOXIC_LATCHED:
            self.state = TOXIC_LATCHED
            self._clear_escalation()
        elif self.state == TOXIC_LATCHED:
            if operator_response == OPERATOR_RELEASE and verdict != "TOXIC":
                released_by = RELEASED_BY_OPERATOR
                self.state = None            # fall through to normal routing below
            # otherwise stay latched regardless of time, evidence, or link

        if self.state != TOXIC_LATCHED:
            # ── normal states: follow the kernel; escalation enters PENDING
            if self.state in (None, COMMIT, HOLD, WAIT):
                if verdict in self.escalation_verdicts:
                    self._enter_pending(t, reason, fresh=True)
                else:
                    self.state = verdict

            # ── ESCALATE_PENDING
            if self.state == ESCALATE_PENDING:
                if operator_response in (OPERATOR_COMMIT, OPERATOR_WAIT):
                    self.state = COMMIT if operator_response == OPERATOR_COMMIT else WAIT
                    released_by = RELEASED_BY_OPERATOR
                    self._clear_escalation()
                elif self.operator_reachable is False:
                    self._enter_fallback(t)
                elif self.next_retry_at is not None and t >= self.next_retry_at:
                    if self.retries < self.p.N_retry:
                        self.retries += 1
                        self.comms_events += 1
                        self.timeout_s = self.p.T_esc * (self.p.backoff_factor ** self.retries)
                        self.next_retry_at = t + self.timeout_s
                    else:
                        self._enter_fallback(t)

            # ── FALLBACK_HOLD
            elif self.state == FALLBACK_HOLD:
                if operator_response in (OPERATOR_COMMIT, OPERATOR_WAIT):
                    # an answer means the operator was reached after all
                    self.state = COMMIT if operator_response == OPERATOR_COMMIT else WAIT
                    released_by = RELEASED_BY_OPERATOR
                    self._clear_escalation()
                elif link_returned:
                    self._enter_pending(t, None, fresh=False)
                elif self.escalation_reason == REASON_UNCERTAINTY:
                    recovered = self.release.update(signal, bar)
                    # the kernel must not itself still be demanding a human
                    if recovered and verdict not in self.escalation_verdicts:
                        dwell_at_release = self.release.dwell
                        self.state = COMMIT
                        released_by = RELEASED_BY_EVIDENCE
                        self._clear_escalation()
                else:
                    # emergency_irreversible / low_confidence_foundation:
                    # evidence cannot release. Hold.
                    self.release.reset()

        rec = GateReceipt(
            tick=self.tick,
            t=t,
            verdict_in=verdict,
            state=self.state,
            action_allowed=(self.state == COMMIT),
            escalated_at=self.escalated_at,
            escalation_reason=self.escalation_reason,
            operator_reachable=self.operator_reachable,
            timeout_s=self.timeout_s,
            retries=self.retries,
            backoff_factor=self.p.backoff_factor,
            fallback_invoked=self.fallback_invoked,
            safe_action_id=self.safe_action_id,
            released_by=released_by,
            dwell_ticks_at_release=dwell_at_release,
            comms_events=self.comms_events,
        ).finalize()
        self.receipts.append(rec)
        return rec


# ─────────────────────────────────────────────────────────────────────────────
# Kit-kernel adapter
# ─────────────────────────────────────────────────────────────────────────────

def escalation_from_kit_verdict(state: str) -> tuple[bool, Optional[str]]:
    """Map a nerva_kernel.Verdict.state to (is_escalation, escalation_reason).

    The kit kernel has no ESCALATE verdict. CONSULT ("axes are contested,
    speak with a person") is its ask-a-human path and maps to `uncertainty`,
    the one reason evidence may later release. The kit's One-Way Brake routes
    to WAIT, not to a human, and is left alone here.
    """
    if state == "CONSULT":
        return True, REASON_UNCERTAINTY
    return False, None


def step_from_kit_verdict(gate: Gate, verdict, *, t: float, **kw) -> GateReceipt:
    """Convenience: feed a nerva_kernel.Verdict straight into a Gate."""
    _, reason = escalation_from_kit_verdict(verdict.state)
    return gate.step(verdict.state, t=t, reason=reason, signal=verdict.C, bar=verdict.tau, **kw)
