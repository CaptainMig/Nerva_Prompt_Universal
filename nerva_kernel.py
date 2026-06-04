"""
nerva_kernel.py
NERVA v11 reference implementation — mixed-state decision-gating kernel.

Use this to verify verdicts produced by an LLM running the NERVA Decision Prompt.
If the LLM's approximation diverges from this implementation on C or tau by more
than 0.05, trust this file.

Starpoint LLC · patent pending
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_AXIS_WEIGHTS = {
    "evidence":        0.30,
    "differentiation": 0.25,
    "falsifiability":  0.20,
    "timing":          0.15,
    "alignment":       0.10,
}

DEFAULT_THRESHOLD_PARAMS = {
    "tau_0":   0.50,   # base threshold
    "alpha":   0.15,   # entropy weight
    "beta":    0.10,   # stakes weight
    "gamma":   0.05,   # irreversibility weight
}

ENTROPY_HOLD_FLOOR = 0.40    # above this entropy, COMMIT becomes HOLD
BRAKE_STAKES_FLOOR = 0.70    # stakes >= this triggers brake check
BRAKE_MARGIN       = 0.10    # extra margin above tau required when brake risk
CONSULT_VARIANCE   = 0.10    # axis variance above this -> CONSULT


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DecisionInput:
    """Everything the kernel needs to score one decision."""

    # Identification
    decision_name:   str
    domain:          str = "general"

    # Per-axis confidences in [0, 1]
    c_evidence:        float = 0.0
    c_differentiation: float = 0.0
    c_falsifiability:  float = 0.0
    c_timing:          float = 0.0
    c_alignment:       float = 0.0

    # Structural factors
    stakes_normalized: float = 0.5     # 0..1 relative to user's baseline
    irreversibility:   int   = 0       # 0 or 1
    toxic_category:    Optional[str] = None  # if set, returns TOXIC

    # Bloch-vector orientation (optional; cosmetic for receipt only)
    theta: float = math.pi / 2         # polar angle
    phi:   float = 0.0                 # azimuthal angle

    # Overrides (rarely used)
    axis_weights:      dict = field(default_factory=lambda: DEFAULT_AXIS_WEIGHTS.copy())
    threshold_params:  dict = field(default_factory=lambda: DEFAULT_THRESHOLD_PARAMS.copy())


@dataclass
class Verdict:
    """The output."""

    state:        str           # COMMIT | HOLD | WAIT | CONSULT | TOXIC
    C:            float         # aggregate confidence
    tau:          float         # adaptive threshold
    S:            float         # von Neumann entropy
    brake:        str           # ENGAGED | DISENGAGED
    r_eff:        tuple         # (rx, ry, rz) of the shrunken Bloch vector
    purity:       float         # |r_eff| = C
    per_axis:     dict          # axis -> confidence
    weights:      dict          # axis -> weight
    reasoning:    str           # plain English
    decision_id:  str           # stable hash of inputs for the ledger
    timestamp:    str
    toxic_reason: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Math
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_confidence(d: DecisionInput) -> float:
    """C = Σ wᵢ · cᵢ over the five axes."""
    w = d.axis_weights
    c = {
        "evidence":        d.c_evidence,
        "differentiation": d.c_differentiation,
        "falsifiability":  d.c_falsifiability,
        "timing":          d.c_timing,
        "alignment":       d.c_alignment,
    }
    return sum(w[k] * c[k] for k in w)


def bloch_vector(theta: float, phi: float) -> tuple[float, float, float]:
    """Unit Bloch vector from spherical angles."""
    return (
        math.sin(theta) * math.cos(phi),
        math.sin(theta) * math.sin(phi),
        math.cos(theta),
    )


def shrink(r: tuple[float, float, float], C: float) -> tuple[float, float, float]:
    """Mixed-state Bloch vector. |r_eff| = C ≤ 1."""
    return (r[0] * C, r[1] * C, r[2] * C)


def von_neumann_entropy(C: float) -> float:
    """
    Entropy of a 2-level mixed state with purity C.
    Eigenvalues of ρ are (1+C)/2 and (1-C)/2.
    """
    if C >= 1.0:
        return 0.0
    if C <= 0.0:
        return 1.0
    p_plus  = (1.0 + C) / 2.0
    p_minus = (1.0 - C) / 2.0
    return -(p_plus * math.log2(p_plus) + p_minus * math.log2(p_minus))


def adaptive_threshold(S: float, stakes: float, irr: int, params: dict) -> float:
    """τ = τ₀ + α·S + β·σ_stakes + γ·irreversibility."""
    return (
        params["tau_0"]
        + params["alpha"] * S
        + params["beta"]  * stakes
        + params["gamma"] * irr
    )


def axis_variance(d: DecisionInput) -> float:
    """Population variance of the five per-axis confidences."""
    vals = [d.c_evidence, d.c_differentiation, d.c_falsifiability,
            d.c_timing,   d.c_alignment]
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def strongest_and_weakest_axis(d: DecisionInput) -> tuple[str, str]:
    """Names of the highest- and lowest-confidence axes."""
    axes = {
        "evidence":        d.c_evidence,
        "differentiation": d.c_differentiation,
        "falsifiability":  d.c_falsifiability,
        "timing":          d.c_timing,
        "alignment":       d.c_alignment,
    }
    return (
        max(axes, key=axes.get),
        min(axes, key=axes.get),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Verdict logic
# ─────────────────────────────────────────────────────────────────────────────

def _decision_id(d: DecisionInput, ts: str) -> str:
    """Stable short hash for the ledger."""
    payload = json.dumps(asdict(d), sort_keys=True) + ts
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _toxic_reasoning(category: str) -> str:
    return (
        f"NERVA refuses to score decisions in the '{category}' category. "
        "See the TOXIC list in the Decision Prompt §7 for the full set of refused categories. "
        "Consider pausing this decision and consulting a person you trust before proceeding."
    )


def _build_reasoning(d: DecisionInput, v: dict) -> str:
    state = v["state"]
    strong, weak = strongest_and_weakest_axis(d)
    nice = {
        "evidence": "evidence specificity",
        "differentiation": "differentiation from the prevailing view",
        "falsifiability": "stated falsifier",
        "timing": "structural timing rationale",
        "alignment": "stake-to-evidence alignment",
    }
    base = (
        f"Aggregate confidence C={v['C']:.2f} versus threshold τ={v['tau']:.2f}; "
        f"von Neumann entropy S={v['S']:.2f}. "
        f"Strongest axis: {nice[strong]}. Weakest axis: {nice[weak]}."
    )
    if state == "COMMIT":
        suffix = (
            " Evidence clears the adaptive threshold and entropy is contained. "
            "Proceed at the size declared."
        )
    elif state == "HOLD":
        suffix = (
            " Confidence is above threshold but entropy remains high. "
            "Consider a partial position, or wait for one more disambiguating signal."
        )
    elif state == "WAIT":
        suffix = (
            " Evidence is below threshold for a commitment of this size and horizon. "
            "Either gather more, or reduce stake to a size consistent with the evidence available."
        )
    elif state == "CONSULT":
        suffix = (
            " Axes are contested — some signals are strong while others are weak. "
            "Speak with a person who has domain expertise before acting."
        )
    else:
        suffix = ""
    if v["brake"] == "ENGAGED":
        suffix += (
            " One-Way Brake engaged: this is an irreversible high-stakes decision; "
            "NERVA requires a margin above the normal threshold and that margin is not met."
        )
    return base + suffix


def score(d: DecisionInput) -> Verdict:
    """Run the kernel."""
    ts = datetime.now(timezone.utc).isoformat()

    # TOXIC short-circuit
    if d.toxic_category:
        return Verdict(
            state="TOXIC",
            C=0.0, tau=0.0, S=1.0,
            brake="DISENGAGED",
            r_eff=(0.0, 0.0, 0.0),
            purity=0.0,
            per_axis={}, weights={},
            reasoning=_toxic_reasoning(d.toxic_category),
            toxic_reason=d.toxic_category,
            decision_id=_decision_id(d, ts),
            timestamp=ts,
        )

    # Math
    C   = aggregate_confidence(d)
    r   = bloch_vector(d.theta, d.phi)
    r_eff = shrink(r, C)
    S   = von_neumann_entropy(C)
    tau = adaptive_threshold(S, d.stakes_normalized, d.irreversibility, d.threshold_params)

    # Brake
    brake_engaged = (
        d.irreversibility == 1
        and d.stakes_normalized >= BRAKE_STAKES_FLOOR
        and C < (tau + BRAKE_MARGIN)
    )
    brake = "ENGAGED" if brake_engaged else "DISENGAGED"

    # State logic
    var = axis_variance(d)
    if brake_engaged:
        state = "WAIT"
    elif var >= CONSULT_VARIANCE and 0.35 <= C <= 0.75:
        state = "CONSULT"
    elif C >= tau and S < ENTROPY_HOLD_FLOOR:
        state = "COMMIT"
    elif C >= tau:
        state = "HOLD"
    else:
        state = "WAIT"

    per_axis = {
        "evidence":        d.c_evidence,
        "differentiation": d.c_differentiation,
        "falsifiability":  d.c_falsifiability,
        "timing":          d.c_timing,
        "alignment":       d.c_alignment,
    }

    v = {"state": state, "C": C, "tau": tau, "S": S, "brake": brake}
    reasoning = _build_reasoning(d, v)

    return Verdict(
        state=state,
        C=round(C, 4),
        tau=round(tau, 4),
        S=round(S, 4),
        brake=brake,
        r_eff=tuple(round(x, 4) for x in r_eff),
        purity=round(C, 4),
        per_axis=per_axis,
        weights=d.axis_weights,
        reasoning=reasoning,
        decision_id=_decision_id(d, ts),
        timestamp=ts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _from_json_stdin() -> DecisionInput:
    raw = sys.stdin.read()
    payload = json.loads(raw)
    return DecisionInput(**payload)


def _print_receipt(v: Verdict, d: DecisionInput) -> None:
    """Pretty plain-text receipt."""
    bar = "═" * 65
    line = "─" * 65
    print(bar)
    print(f"  NERVA · DECISION RECEIPT          {v.timestamp[:19].replace('T', ' ')}")
    print(bar)
    print()
    print("  WHAT WAS SCORED")
    print(f"  {line}")
    print(f"  {d.decision_name}")
    print(f"  Domain: {d.domain:<20}  Stakes (norm): {d.stakes_normalized:.2f}")
    print(f"  Irreversibility: {'yes' if d.irreversibility else 'no'}")
    print()
    print()
    print(f"            ┌─────────────────────────┐")
    print(f"            │                         │")
    print(f"            │       {v.state:^11s}       │")
    print(f"            │                         │")
    print(f"            └─────────────────────────┘")
    print()
    print()
    print("  METRICS")
    print(f"  {line}")
    print(f"  C     = {v.C:.4f}        (aggregate confidence)")
    print(f"  tau   = {v.tau:.4f}        (adaptive threshold)")
    print(f"  S     = {v.S:.4f}        (von Neumann entropy)")
    print(f"  Brake : {v.brake}")
    print()
    print("  REASONING")
    print(f"  {line}")
    # word-wrap reasoning to 63 chars
    words = v.reasoning.split()
    line_buf = "  "
    for w in words:
        if len(line_buf) + len(w) + 1 > 65:
            print(line_buf)
            line_buf = "  " + w
        else:
            line_buf += (" " if line_buf != "  " else "") + w
    if line_buf.strip():
        print(line_buf)
    print()
    print("  PROVENANCE")
    print(f"  {line}")
    for axis, c in v.per_axis.items():
        w = v.weights.get(axis, 0)
        print(f"  c_{axis:<16s} = {c:.3f}      (weight {w:.2f})")
    print()
    print(f"  r_eff = ({v.r_eff[0]:+.3f}, {v.r_eff[1]:+.3f}, {v.r_eff[2]:+.3f})    |r| = {v.purity:.3f}")
    print(f"  rho   = (1/2)(I + r_eff·sigma)")
    print()
    print("  RECEIPT (log this for your ledger)")
    print(f"  {line}")
    print(f"  Decision ID: {v.decision_id}")
    print( "  Action taken:    [ ] Committed  [ ] Resized  [ ] Cancelled  [ ] Overrode")
    print( "  Outcome:         [ ] Win  [ ] Loss  [ ] N/A")
    print( "  P&L: __________")
    print()
    print( "  NERVA v11 · Starpoint LLC · patent pending")
    print(bar)


def main() -> int:
    """
    CLI:
        python nerva_kernel.py < decision.json          # pretty receipt
        python nerva_kernel.py --json < decision.json   # JSON verdict
        python nerva_kernel.py --example                # print Antonelli example
    """
    if "--example" in sys.argv:
        ex = DecisionInput(
            decision_name="Kalshi · Monaco GP · YES Antonelli @ 18¢ · $25,000",
            domain="prediction-market",
            c_evidence=0.25,
            c_differentiation=0.20,
            c_falsifiability=0.55,
            c_timing=0.30,
            c_alignment=0.10,
            stakes_normalized=0.85,
            irreversibility=1,
            theta=math.pi / 2,
            phi=0.4,
        )
        v = score(ex)
        _print_receipt(v, ex)
        return 0

    d = _from_json_stdin()
    v = score(d)
    if "--json" in sys.argv:
        out = asdict(v)
        print(json.dumps(out, indent=2))
    else:
        _print_receipt(v, d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
