"""Benchmark: wall-clock time per NERVA verdict, pure Python, including receipt build and hash.

Run from the Nerva_Prompt_Universal repo root:
    python3 bench_kernel.py
"""
import random, statistics, sys, time, platform
sys.path.insert(0, ".")
import nerva_kernel as nk

random.seed(7)
inputs = [
    nk.DecisionInput(
        decision_name=f"d{i}",
        c_evidence=random.random(), c_differentiation=random.random(),
        c_falsifiability=random.random(), c_timing=random.random(),
        c_alignment=random.random(),
        stakes_normalized=random.random(), irreversibility=random.randint(0, 1),
    )
    for i in range(1000)
]
for d in inputs[:100]:
    nk.score(d)  # warm-up

N = 20000
samples = []
for k in range(N):
    d = inputs[k % 1000]
    t0 = time.perf_counter_ns()
    nk.score(d)
    samples.append(time.perf_counter_ns() - t0)
samples.sort()
print(f"nerva_kernel.score(): {N} calls, pure Python {platform.python_version()}, {platform.machine()}")
print(f"median {statistics.median(samples)/1000:.1f} us | p95 {samples[int(N*0.95)]/1000:.1f} us | "
      f"p99 {samples[int(N*0.99)]/1000:.1f} us | max {samples[-1]/1000:.1f} us")
