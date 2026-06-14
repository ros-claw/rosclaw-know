---
pattern_id: multi_stage_cc_cv_fast_charging
safety_label: Battery_Capacity_Fade
applicable_symptoms: [multi_stage_cc_cv_fast_charging]
domain: Systems_Compute
source: curated
---

# Aggressive constant-current fast-charging accelerates capacity fade via lithium plating at high SOC on graphite or silicon anodes

**Domain**: `Systems_Compute`
**Safety label**: `Battery_Capacity_Fade`

## Fix

Replace single-stage CC charging with a multi-stage protocol: keep constant current only below ~70 % SOC, then taper current as a function of SOC (e.g. I(SOC) = I_max * (1 - SOC)^0.5) before switching to constant-voltage hold. Add a temperature-aware current limiter that derates above 35 °C anode-surface temperature. Pulse-charging with short rest intervals also relieves lithium concentration gradients near the anode.

## Anti-pattern

Holding the same 4C constant-current target through the whole 10→80 % SOC window — once SOC > 70 % the anode-side overpotential drops below 0 V vs. Li/Li+ and metallic lithium plates onto the graphite surface, irreversibly consuming cyclable lithium.

## Cross-domain analogies (curated)

- **Control_Locomotion** → Same shape as gain-scheduled PID: full gain near the operating point, taper as you approach the saturation boundary.
  - related fix: Derate the control effort (charging current) as the state (SOC) approaches the unsafe regime where plating dominates.

## Patch

```diff
--- multi_stage_cc_cv_fast_charging.before.py+++ multi_stage_cc_cv_fast_charging.after.py@@ -1,4 +1,11 @@-def fast_charge(cell, target_soc=0.80):
+def fast_charge(cell, target_soc=0.80, taper_above_soc=0.70):
     while cell.soc < target_soc:
-        cell.apply_current(4 * cell.capacity_Ah)  # 4C flat
+        if cell.surface_temp_C > 35.0:
+            i_lim = 1.0 * cell.capacity_Ah                  # thermal derate
+        elif cell.soc < taper_above_soc:
+            i_lim = 4.0 * cell.capacity_Ah                  # CC stage
+        else:
+            # SOC-dependent taper toward CV hold
+            i_lim = 4.0 * cell.capacity_Ah * (1 - cell.soc) ** 0.5
+        cell.apply_current(i_lim)
         cell.step(dt=1.0)

```
