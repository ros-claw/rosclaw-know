---
title: Test PID Tuning Pattern (E2E-KNOW-002)
source_type: paper
domain: Control_PID
---

# Synthetic PID Tuning Document

This file is a test fixture for E2E-KNOW-002 (incremental ingest).

## Symptom

PID controller exhibits integral wind-up when actuator saturates during
large step responses, leading to overshoot and torque oscillation.

## Fix

Clamp the integral accumulator to ±I_max whenever the actuator is in
saturation; reset on direction change. Add a feed-forward term for the
expected steady-state torque to reduce integral burden.

## Anti-pattern

Disabling the integral term entirely. This causes steady-state error
that the proportional term cannot eliminate.

## Cross-domain Analogy

Same wind-up dynamics appear in neural-net optimizers (RMSProp/Adam
without momentum clamping) and battery state-of-charge estimators.
