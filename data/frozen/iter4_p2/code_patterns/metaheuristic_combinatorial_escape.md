---
pattern_id: metaheuristic_combinatorial_escape
safety_label: Combinatorial_Local_Optimum
applicable_symptoms: [metaheuristic_combinatorial_escape]
domain: Planning_Decision
source: curated
---

# Greedy / local-search on combinatorial scheduling (job-shop, TSP, VRP) stagnates after the descent phase; objective plateaus well above the known optimum

**Domain**: `Planning_Decision`
**Safety label**: `Combinatorial_Local_Optimum`

## Fix

Replace pure greedy descent with a neighborhood-escape metaheuristic. For job-shop specifically: tabu search with critical-path moves (swap two consecutive operations on the critical path) — the tabu list of size ~7-10 prevents reversal cycles, and the critical-path restriction means every move directly attacks the bottleneck. Alternatives that also work: GA with operation-based or precedence-preserving crossover, OR simulated annealing with shift moves (insert an operation at a different position in the sequence). The deciding choice between them is implementation effort, not solution quality at the abz5 / ft10 / la0X benchmark scale.

## Anti-pattern

Running the same greedy dispatch rule longer, or restarting it from a different seed — the rule converges to the same family of local optima because the move set never crosses the basin boundary.

## Cross-domain analogies (curated)

- **Learning_Training** → Tabu / SA / GA are to combinatorial search what exploration noise + replay are to reinforcement learning: structured escape from local optima.
  - related fix: Don't tune the greedy heuristic further; switch the outer-loop algorithm to one with a non-trivial neighborhood and a memory of where it's been.

## Patch

```diff
--- metaheuristic_combinatorial_escape.before.py+++ metaheuristic_combinatorial_escape.after.py@@ -1,6 +1,11 @@-# greedy first-available — saturates around 1400 on abz5 (opt=1234)
-schedule = []
-for op in operations:
-    earliest = max(machine_avail[op.machine], job_avail[op.job])
-    schedule.append((op, earliest))
-    machine_avail[op.machine] = job_avail[op.job] = earliest + op.dur
+# tabu search with critical-path moves — reaches 1234-1260 on abz5
+best = greedy_initial_schedule(operations)
+tabu = deque(maxlen=10)
+for _ in range(MAX_ITERS):
+    cp = critical_path(best)                       # bottleneck ops
+    moves = [(i, i+1) for i in range(len(cp)-1)
+             if (cp[i].job, cp[i+1].job) not in tabu]
+    cand = min((apply_move(best, m) for m in moves), key=makespan)
+    if makespan(cand) < makespan(best):
+        best = cand
+        tabu.append((cand.swapped_jobs))

```
