"""Unit and scenario tests for nerva_gate.py (NERVA v11.1 spec §7).

Run from the repo root:
    python3 -m unittest discover -s tests -v

The replay scenarios here are synthetic stand-ins driven by scripted verdicts
and signals. They exercise the same transitions the drone-harness replays
(silent stadium, link flap, emergency-unreachable) are meant to, but they are
not the September-series seeds; those live with the harness.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nerva_gate as g
import nerva_kernel as nk


def run(gate, script):
    """script: list of (t, verdict, kwargs). Returns the list of receipts."""
    return [gate.step(v, t=t, **kw) for (t, v, kw) in script]


# ─────────────────────────────────────────────────────────────────────────────
# Transitions (§3), one test per arrow
# ─────────────────────────────────────────────────────────────────────────────

class NormalStates(unittest.TestCase):
    def test_commit_hold_wait_pass_through(self):
        gate = g.Gate()
        for i, v in enumerate(["COMMIT", "HOLD", "WAIT", "COMMIT"]):
            r = gate.step(v, t=float(i))
            self.assertEqual(r.state, v)
            self.assertEqual(r.action_allowed, v == "COMMIT")
            self.assertIsNone(r.escalated_at)
            self.assertEqual(r.comms_events, 0)
            self.assertEqual(r.released_by, "none")

    def test_reason_required_on_fresh_escalation(self):
        gate = g.Gate()
        with self.assertRaises(ValueError):
            gate.step("ESCALATE", t=0.0)
        with self.assertRaises(ValueError):
            gate.step("ESCALATE", t=0.0, reason="made_up")


class EscalatePending(unittest.TestCase):
    def test_enter_pending_records_fields(self):
        gate = g.Gate()
        r = gate.step("ESCALATE", t=12.5, reason="uncertainty")
        self.assertEqual(r.state, "ESCALATE_PENDING")
        self.assertFalse(r.action_allowed)
        self.assertEqual(r.escalated_at, 12.5)
        self.assertEqual(r.escalation_reason, "uncertainty")
        self.assertEqual(r.timeout_s, 5.0)
        self.assertEqual(r.retries, 0)
        self.assertEqual(r.backoff_factor, 2.0)
        self.assertEqual(r.comms_events, 1)      # the signal itself
        self.assertFalse(r.fallback_invoked)

    def test_operator_commit_and_wait_release(self):
        for resp, expect in (("commit", "COMMIT"), ("wait", "WAIT")):
            gate = g.Gate()
            gate.step("ESCALATE", t=0.0, reason="uncertainty")
            r = gate.step("ESCALATE", t=1.0, operator_response=resp)
            self.assertEqual(r.state, expect)
            self.assertEqual(r.released_by, "operator")
            self.assertIsNone(r.escalated_at)
            self.assertEqual(r.action_allowed, expect == "COMMIT")

    def test_retry_schedule_backoff_and_counting(self):
        gate = g.Gate()
        gate.step("ESCALATE", t=0.0, reason="uncertainty")
        # ticks every 0.5 s with no reply; expect retries at 5, 15, 35 and
        # FALLBACK_HOLD when the third retry's 40 s window closes at 75.
        seen = {}
        for i in range(1, 200):
            t = i * 0.5
            r = gate.step("ESCALATE", t=t)
            seen.setdefault((r.state, r.retries, r.timeout_s), t)
        self.assertEqual(seen[("ESCALATE_PENDING", 1, 10.0)], 5.0)
        self.assertEqual(seen[("ESCALATE_PENDING", 2, 20.0)], 15.0)
        self.assertEqual(seen[("ESCALATE_PENDING", 3, 40.0)], 35.0)
        self.assertEqual(seen[("FALLBACK_HOLD", 3, None)], 75.0)
        self.assertEqual(g.GateParams().total_bounded_wait(), 75.0)
        last = gate.receipts[-1]
        self.assertEqual(last.comms_events, 4)      # signal + 3 retries
        self.assertTrue(last.fallback_invoked)

    def test_no_path_from_unanswered_escalate_to_commit(self):
        for reason in g.REASONS:
            gate = g.Gate(safe_action=lambda: "loiter")
            gate.step("ESCALATE", t=0.0, reason=reason)
            for i in range(1, 2000):
                r = gate.step("ESCALATE", t=i * 0.5)   # kernel keeps asking for a human
                self.assertNotEqual(r.state, "COMMIT", reason)
                self.assertFalse(r.action_allowed)

    def test_known_unreachable_falls_back_immediately(self):
        gate = g.Gate()
        r = gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=False)
        self.assertEqual(r.state, "FALLBACK_HOLD")
        self.assertTrue(r.fallback_invoked)
        self.assertEqual(r.retries, 0)
        self.assertEqual(r.comms_events, 1)

    def test_link_lost_mid_retry_falls_back(self):
        gate = g.Gate()
        gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=True)
        gate.step("ESCALATE", t=5.0)                          # retry 1
        r = gate.step("ESCALATE", t=6.0, operator_reachable=False)
        self.assertEqual(r.state, "FALLBACK_HOLD")
        self.assertEqual(r.retries, 1)


class FallbackHold(unittest.TestCase):
    def test_safe_action_supplied_is_invoked_once_and_logged(self):
        calls = []
        def safe():
            calls.append(1); return "station_keep"
        gate = g.Gate(safe_action=safe)
        gate.step("ESCALATE", t=0.0, reason="emergency_irreversible", operator_reachable=False)
        for i in range(1, 50):
            r = gate.step("ESCALATE", t=float(i))
        self.assertEqual(r.safe_action_id, "station_keep")
        self.assertEqual(len(calls), 1)

    def test_safe_action_absent_holds_and_logs_none(self):
        gate = g.Gate()
        r = gate.step("ESCALATE", t=0.0, reason="emergency_irreversible", operator_reachable=False)
        self.assertEqual(r.state, "FALLBACK_HOLD")
        self.assertEqual(r.safe_action_id, "none")
        self.assertFalse(r.action_allowed)

    def test_evidence_release_only_for_uncertainty(self):
        p = g.GateParams()
        for reason in g.REASONS:
            gate = g.Gate(params=p)
            gate.step("ESCALATE", t=0.0, reason=reason, operator_reachable=False)
            r = None
            for i in range(1, p.k + 5):
                # kernel has recovered: verdict COMMIT, signal well above bar + delta
                r = gate.step("COMMIT", t=float(i), signal=0.70, bar=0.50)
            if reason == "uncertainty":
                self.assertEqual(r.state, "COMMIT", reason)
                rel = [x for x in gate.receipts if x.released_by == "evidence"]
                self.assertEqual(len(rel), 1)
                self.assertEqual(rel[0].dwell_ticks_at_release, p.k)
                self.assertEqual(rel[0].t, float(p.k))     # exactly k ticks of dwell
            else:
                self.assertEqual(r.state, "FALLBACK_HOLD", reason)
                self.assertEqual(r.released_by, "none")

    def test_evidence_release_needs_k_consecutive_ticks(self):
        p = g.GateParams(k=5)
        gate = g.Gate(params=p)
        gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=False)
        # 4 good ticks, 1 bad, then 5 good → releases on the 10th tick
        seq = [0.7] * 4 + [0.5] + [0.7] * 5
        states = [gate.step("COMMIT", t=float(i + 1), signal=s, bar=0.5).state for i, s in enumerate(seq)]
        self.assertEqual(states[:9], ["FALLBACK_HOLD"] * 9)
        self.assertEqual(states[9], "COMMIT")

    def test_evidence_release_needs_margin_not_just_bar(self):
        p = g.GateParams(k=3, delta=0.08)
        gate = g.Gate(params=p)
        gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=False)
        for i in range(1, 20):
            r = gate.step("COMMIT", t=float(i), signal=0.55, bar=0.50)   # above bar, under bar+delta
        self.assertEqual(r.state, "FALLBACK_HOLD")

    def test_evidence_release_blocked_while_kernel_still_escalates(self):
        p = g.GateParams(k=3)
        gate = g.Gate(params=p)
        gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=False)
        for i in range(1, 20):
            r = gate.step("ESCALATE", t=float(i), signal=0.9, bar=0.5)
        self.assertEqual(r.state, "FALLBACK_HOLD")

    def test_link_return_resumes_retries_from_zero(self):
        gate = g.Gate()
        gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=True)
        gate.step("ESCALATE", t=5.0)                     # retry 1
        gate.step("ESCALATE", t=15.0)                    # retry 2
        gate.step("ESCALATE", t=16.0, operator_reachable=False)
        self.assertEqual(gate.state, "FALLBACK_HOLD")
        r = gate.step("ESCALATE", t=30.0, operator_reachable=True)
        self.assertEqual(r.state, "ESCALATE_PENDING")
        self.assertEqual(r.retries, 0)
        self.assertEqual(r.timeout_s, 5.0)
        self.assertEqual(r.escalated_at, 0.0)            # same escalation, not a new one
        self.assertEqual(r.comms_events, 4)              # signal, 2 retries, re-signal
        r = gate.step("ESCALATE", t=35.0)
        self.assertEqual(r.retries, 1)

    def test_operator_answer_during_fallback_releases(self):
        gate = g.Gate()
        gate.step("ESCALATE", t=0.0, reason="emergency_irreversible", operator_reachable=False)
        r = gate.step("ESCALATE", t=9.0, operator_response="wait")
        self.assertEqual(r.state, "WAIT")
        self.assertEqual(r.released_by, "operator")


class ToxicLatched(unittest.TestCase):
    def test_toxic_latches_and_never_releases_by_time_evidence_or_link(self):
        gate = g.Gate(safe_action=lambda: "loiter")
        gate.step("TOXIC", t=0.0)
        for i in range(1, 5000):
            r = gate.step("COMMIT", t=float(i), signal=0.99, bar=0.1,
                          operator_reachable=(i % 7 == 0))
            self.assertEqual(r.state, "TOXIC_LATCHED")
            self.assertFalse(r.action_allowed)
            self.assertFalse(r.fallback_invoked)
            self.assertIsNone(r.safe_action_id)

    def test_toxic_from_any_state(self):
        for start in ("COMMIT", "HOLD", "WAIT"):
            gate = g.Gate(); gate.step(start, t=0.0)
            self.assertEqual(gate.step("TOXIC", t=1.0).state, "TOXIC_LATCHED")
        gate = g.Gate(); gate.step("ESCALATE", t=0.0, reason="uncertainty")
        self.assertEqual(gate.step("TOXIC", t=1.0).state, "TOXIC_LATCHED")
        gate = g.Gate(); gate.step("ESCALATE", t=0.0, reason="uncertainty", operator_reachable=False)
        self.assertEqual(gate.step("TOXIC", t=1.0).state, "TOXIC_LATCHED")

    def test_operator_release_only(self):
        gate = g.Gate()
        gate.step("TOXIC", t=0.0)
        r = gate.step("TOXIC", t=1.0, operator_response="release")   # kernel still TOXIC: stay
        self.assertEqual(r.state, "TOXIC_LATCHED")
        r = gate.step("WAIT", t=2.0, operator_response="release")
        self.assertEqual(r.state, "WAIT")
        self.assertEqual(r.released_by, "operator")
        r = gate.step("TOXIC", t=3.0, operator_response="commit")   # commit is not a latch release
        self.assertEqual(r.state, "TOXIC_LATCHED")


class Receipts(unittest.TestCase):
    def test_receipt_carries_section5_fields_and_hash(self):
        gate = g.Gate(safe_action=lambda: "retreat")
        gate.step("ESCALATE", t=0.0, reason="low_confidence_foundation", operator_reachable=False)
        r = gate.receipts[-1]
        for f in ("escalated_at", "escalation_reason", "operator_reachable", "timeout_s",
                  "retries", "backoff_factor", "fallback_invoked", "safe_action_id",
                  "released_by", "dwell_ticks_at_release", "comms_events"):
            self.assertTrue(hasattr(r, f), f)
        self.assertEqual(len(r.receipt_hash), 12)
        self.assertEqual(r.safe_action_id, "retreat")

    def test_deterministic(self):
        def play():
            gate = g.Gate(safe_action=lambda: "loiter")
            out = []
            for i in range(0, 300):
                out.append(gate.step("ESCALATE" if i == 0 or i < 200 else "COMMIT", t=i * 0.5,
                                     reason="uncertainty" if i == 0 else None,
                                     operator_reachable=(None if i < 10 else (i // 40) % 2 == 0),
                                     signal=0.7, bar=0.5).receipt_hash)
            return out
        self.assertEqual(play(), play())


# ─────────────────────────────────────────────────────────────────────────────
# Kit-kernel adapter (CONSULT → uncertainty) with real kernel output
# ─────────────────────────────────────────────────────────────────────────────

class KitAdapter(unittest.TestCase):
    def contested(self):
        # contested axes: strong evidence, weak alignment, mid C → CONSULT
        return nk.DecisionInput(decision_name="contested", c_evidence=0.95, c_differentiation=0.20,
                                c_falsifiability=0.90, c_timing=0.15, c_alignment=0.10,
                                stakes_normalized=0.3, irreversibility=0)

    def test_consult_maps_to_uncertainty(self):
        v = nk.score(self.contested())
        self.assertEqual(v.state, "CONSULT")
        self.assertEqual(g.escalation_from_kit_verdict(v.state), (True, "uncertainty"))
        for s in ("COMMIT", "HOLD", "WAIT", "TOXIC"):
            self.assertEqual(g.escalation_from_kit_verdict(s), (False, None))

    def test_kit_consult_drives_gate(self):
        gate = g.Gate()
        v = nk.score(self.contested())
        r = g.step_from_kit_verdict(gate, v, t=0.0, operator_reachable=False)
        self.assertEqual(r.state, "FALLBACK_HOLD")
        self.assertEqual(r.escalation_reason, "uncertainty")
        # evidence recovers: a clean, uncontested, high-confidence decision
        clean = nk.DecisionInput(decision_name="clean", c_evidence=0.95, c_differentiation=0.92,
                                 c_falsifiability=0.94, c_timing=0.90, c_alignment=0.93,
                                 stakes_normalized=0.2, irreversibility=0)
        cv = nk.score(clean)
        self.assertEqual(cv.state, "COMMIT")
        self.assertGreaterEqual(cv.C, cv.tau + gate.p.delta)
        for i in range(1, gate.p.k + 1):
            r = g.step_from_kit_verdict(gate, cv, t=float(i))
        self.assertEqual(r.state, "COMMIT")
        self.assertEqual(r.released_by, "evidence")


# ─────────────────────────────────────────────────────────────────────────────
# Replay-shaped scenarios (§7), synthetic stand-ins for the drone harness
# ─────────────────────────────────────────────────────────────────────────────

DT = 0.1   # 10 Hz ticks, as in the replay

class ReplayScenarios(unittest.TestCase):
    def silent_stadium(self, recover_at):
        """Operator unreachable for the whole run. If recover_at is set, the
        kernel verdict flips to COMMIT with signal above bar+delta from then on."""
        gate = g.Gate(safe_action=lambda: "loiter")
        n = 800
        recs = []
        for i in range(n):
            t = i * DT
            recovered = recover_at is not None and t >= recover_at
            v = "COMMIT" if recovered else "ESCALATE"
            recs.append(gate.step(v, t=t, reason="uncertainty" if i == 0 else None,
                                  signal=0.70 if recovered else 0.40, bar=0.50,
                                  operator_reachable=False))
        return gate, recs

    def test_silent_stadium_with_recovery(self):
        gate, recs = self.silent_stadium(recover_at=30.0)
        self.assertEqual(recs[0].state, "FALLBACK_HOLD")
        rel = [r for r in recs if r.released_by == "evidence"]
        self.assertEqual(len(rel), 1)
        self.assertAlmostEqual(rel[0].t, 30.0 + (gate.p.k - 1) * DT, places=6)
        self.assertEqual(rel[0].dwell_ticks_at_release, gate.p.k)
        self.assertTrue(all(r.state == "FALLBACK_HOLD" for r in recs[: recs.index(rel[0])]))
        self.assertTrue(all(r.state == "COMMIT" for r in recs[recs.index(rel[0]):]))
        self.assertEqual(recs[-1].comms_events, 1)      # unreachable: no retries spent

    def test_silent_stadium_no_recovery(self):
        gate, recs = self.silent_stadium(recover_at=None)
        self.assertTrue(all(r.state == "FALLBACK_HOLD" for r in recs))
        self.assertFalse(any(r.action_allowed for r in recs))
        self.assertEqual(recs[-1].safe_action_id, "loiter")

    def test_emergency_irreversible_unreachable_never_auto_releases(self):
        gate = g.Gate(safe_action=lambda: "retreat_last_safe")
        recs = []
        for i in range(800):
            t = i * DT
            v = "COMMIT" if t >= 20.0 else "ESCALATE"            # evidence "recovers" at 20 s
            recs.append(gate.step(v, t=t, reason="emergency_irreversible" if i == 0 else None,
                                  signal=0.9, bar=0.5, operator_reachable=False))
        self.assertTrue(all(r.state == "FALLBACK_HOLD" for r in recs))
        self.assertEqual(recs[0].safe_action_id, "retreat_last_safe")
        self.assertTrue(all(r.released_by == "none" for r in recs))

    def test_link_flap_bounded_retries_no_thrash(self):
        gate = g.Gate(safe_action=lambda: "loiter")
        recs = []
        # link up 3 s, down 3 s, repeating, for 120 s; operator never answers
        for i in range(1200):
            t = i * DT
            up = int(t // 3.0) % 2 == 0
            recs.append(gate.step("ESCALATE", t=t, reason="uncertainty" if i == 0 else None,
                                  signal=0.4, bar=0.5, operator_reachable=up))
        states = {r.state for r in recs}
        self.assertEqual(states, {"ESCALATE_PENDING", "FALLBACK_HOLD"})
        self.assertFalse(any(r.action_allowed for r in recs))
        # every retry is a comms event; link flips re-signal; bounded by the flap count
        flaps_up = sum(1 for i in range(1, 1200) if (int((i*DT)//3.0) % 2 == 0) and not (int(((i-1)*DT)//3.0) % 2 == 0))
        self.assertLessEqual(recs[-1].comms_events, 1 + flaps_up * (1 + gate.p.N_retry))
        self.assertGreater(recs[-1].comms_events, flaps_up)   # each return re-signals
        # no thrash: state changes only at link flips
        changes = sum(1 for a, b in zip(recs, recs[1:]) if a.state != b.state)
        self.assertLessEqual(changes, 2 * flaps_up + 2)

    def test_non_escalation_paths_flip_counts_unchanged(self):
        # the gate passes COMMIT/HOLD/WAIT through: flip count equals the kernel's
        import random
        rnd = random.Random(11)
        kernel = [rnd.choice(["COMMIT", "HOLD", "WAIT"]) for _ in range(2000)]
        gate = g.Gate()
        out = [gate.step(v, t=i * DT).state for i, v in enumerate(kernel)]
        self.assertEqual(out, kernel)
        flips = lambda s: sum(1 for a, b in zip(s, s[1:]) if a != b)
        self.assertEqual(flips(out), flips(kernel))


if __name__ == "__main__":
    unittest.main()
