---
pattern_id: flash_attention_tiled_softmax
safety_label: Memory_Exhaustion
applicable_symptoms: [flash_attention_tiled_softmax]
domain: Systems_Compute
source: curated
---

# Transformer self-attention layer materializes the full NxN matrix in HBM at long context length, causing CUDA OOM and HBM-bandwidth-bound (not compute-bound) inference throughput

**Domain**: `Systems_Compute`
**Safety label**: `Memory_Exhaustion`

## Fix

Adopt FlashAttention-style TILED ONLINE-SOFTMAX attention: split Q and K/V into blocks that fit in SRAM, compute partial softmax incrementally with rescaling, and never materialize the full attention matrix in HBM. As a SECONDARY win, the SRAM-resident softmax becomes compute-bound (good for arithmetic intensity). Fallback when tiled attention is unavailable: SLIDING-WINDOW attention or circular-buffer KV-cache truncation to cap the effective sequence length seen by the softmax.

## Anti-pattern

Casting QK to fp16 to halve memory — saves only 2x and degrades accuracy; the real problem is the N^2 materialization, not precision. Also: bumping GPU memory limit is a non-fix that just delays the OOM.

## Cross-domain analogies (curated)

- **Memory_Reasoning** → Online softmax is mathematically a streaming reduction: maintain (max, sum) statistics and rescale partials. Same pattern as streaming variance (Welford's algorithm).
  - related fix: Treat the softmax denominator as a running statistic updated incrementally rather than recomputed.
- **Systems_Compute** → HBM-bandwidth-bound becomes compute-bound when working set fits in SRAM. Same trade-off as cache-blocking in dense linear algebra.
  - related fix: Size block_size to match L2/SRAM capacity divided by matrix-element bytes.

## Patch

```diff
--- flash_attention_tiled_softmax.before.py+++ flash_attention_tiled_softmax.after.py@@ -1,3 +1,7 @@-def attention(Q, K, V):
-    scores = Q @ K.transpose(-2,-1) / sqrt(dim)  # NxN materialized
-    return softmax(scores) @ V  # OOM at large N
+def flash_attention(Q, K, V, block_size=128):
+    out = zeros_like(V)
+    for j in range(0, K.shape[-2], block_size):
+        Kj = K[..., j:j+block_size, :]
+        Vj = V[..., j:j+block_size, :]
+        out = online_softmax_update(out, Q, Kj, Vj)
+    return out

```
