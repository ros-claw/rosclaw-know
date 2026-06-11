---
pattern_id: simd_aes_ni_hardware_crypto
safety_label: Crypto_Throughput_Bottleneck
applicable_symptoms: [simd_aes_ni_hardware_crypto]
domain: Systems_Compute
source: curated
---

# Pure-software block-cipher round functions saturate CPU on SubBytes/MixColumns; throughput plateaus at <100 MB/s on x86_64

**Domain**: `Systems_Compute`
**Safety label**: `Crypto_Throughput_Bottleneck`

## Fix

Replace the SBOX-table + ShiftRows + MixColumns inner loop with x86 AES-NI intrinsics (`_mm_aesenc_si128`, `_mm_aesenclast_si128`) — one AESENC instruction per round retires in ~3 cycles, delivering 1-2 GB/s per core. Use `_mm_clmulepi64_si128` (PCLMULQDQ) for the GHASH multiply in GCM mode. For platforms without AES-NI, fall back to a bitsliced implementation (parallel 8-block lanes) rather than the scalar SBOX-table loop.

## Anti-pattern

Hand-rolled SBOX lookup tables in C with `unsigned char state[16]` — even with `-O3` the compiler can't vectorize the 16-byte SBOX indirection, so the loop bottlenecks on L1 latency at ~80 MB/s.

## Cross-domain analogies (curated)

- **Learning_Training** → Same insight as moving a softmax from Python to fused CUDA kernels: when the hot path is a tight 16-byte loop, the fix is hardware-specific intrinsics, not a smarter algorithm.
  - related fix: Profile to confirm the bottleneck is the round function, then drop to platform intrinsics. Keep a portable fallback (bitsliced) for non-x86 / unprivileged targets.

## Patch

```diff
--- simd_aes_ni_hardware_crypto.before.py+++ simd_aes_ni_hardware_crypto.after.py@@ -1,7 +1,9 @@-// scalar AES round — SBOX lookup serialises on L1 latency
-static void aes_round(uint8_t s[16], const uint8_t k[16]) {
-    for (int i = 0; i < 16; ++i) s[i] = SBOX[s[i]];
-    shift_rows(s);
-    mix_columns(s);
-    for (int i = 0; i < 16; ++i) s[i] ^= k[i];
+// AES-NI round — single instruction, ~3 cycle latency, ~1.5 GB/s
+#include <wmmintrin.h>
+static __m128i aes_round_ni(__m128i state, __m128i round_key) {
+    return _mm_aesenc_si128(state, round_key);
 }
+// GCM multiply: use PCLMULQDQ
+static __m128i ghash_mul(__m128i a, __m128i b) {
+    return _mm_clmulepi64_si128(a, b, 0x00);
+}

```
