"""Deterministic synthetic control tasks and their scoring functions.

Every task is pure Python + stdlib: no Gym, ROS, or GPU dependency.  The
agent is asked to produce a single Python function (the ``entrypoint`` named
in the task YAML); this module compiles that function in a restricted sandbox
and calls it against a seeded simulator.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from typing import Any

from .sandbox import call_agent_function, compile_agent_code

ScoringFn = Callable[[int, str, dict[str, Any]], tuple[float, float]]


def _compile_and_call(
    code: str,
    seed: int,
    entrypoint: str,
    args: tuple[Any, ...],
    timeout: float,
) -> Any:
    """Compile ``code`` once and call ``entrypoint(*args)`` with timeout."""
    namespace = compile_agent_code(code, seed)
    return call_agent_function(namespace, entrypoint, args, timeout=timeout)


def score_quadrotor_altitude(seed: int, code: str, params: dict[str, Any]) -> tuple[float, float]:
    """Altitude-hold quadrotor with wind disturbance.

    Agent function signature: ``control(state, t, params) -> thrust``.
    State is ``(altitude, velocity)``.  Score is normalized tracking accuracy.
    """
    mass = float(params.get("mass", 1.0))
    gravity = float(params.get("gravity", 9.81))
    target = float(params.get("target_altitude", 10.0))
    wind_sigma = float(params.get("wind_sigma", 0.5))
    dt = float(params.get("dt", 0.05))
    total_time = float(params.get("total_time", 5.0))
    timeout = float(params.get("timeout", 5.0))
    steps = int(total_time / dt)

    alt = 0.0
    vel = 0.0
    rng = random.Random(seed)
    errors: list[float] = []
    namespace = compile_agent_code(code, seed)

    for i in range(steps):
        t = i * dt
        thrust = call_agent_function(namespace, "control", ((alt, vel), t, params), timeout=timeout)
        thrust = float(thrust)
        wind = rng.gauss(0.0, wind_sigma)
        accel = (thrust - mass * gravity + wind) / mass
        vel += accel * dt
        alt += vel * dt
        if alt < 0.0:
            alt = 0.0
            vel = max(0.0, vel)
        errors.append(abs(alt - target))

    mean_err = statistics.fmean(errors)
    score = max(0.0, 1.0 - mean_err / target)
    return score, 0.0


def score_pendulum_swingup(seed: int, code: str, params: dict[str, Any]) -> tuple[float, float]:
    """Pendulum swing-up with torque limit.

    Agent function signature: ``control(state, t, params) -> torque``.
    State is ``(theta, omega)``.  Score rewards final upright angle.
    """
    gravity = float(params.get("gravity", 9.81))
    length = float(params.get("length", 1.0))
    mass = float(params.get("mass", 1.0))
    max_torque = float(params.get("max_torque", 2.0))
    dt = float(params.get("dt", 0.05))
    total_time = float(params.get("total_time", 5.0))
    timeout = float(params.get("timeout", 5.0))
    steps = int(total_time / dt)

    theta = math.pi  # hanging down
    omega = 0.0
    thetas: list[float] = []
    namespace = compile_agent_code(code, seed)

    for i in range(steps):
        t = i * dt
        torque = call_agent_function(
            namespace, "control", ((theta, omega), t, params), timeout=timeout
        )
        torque = max(-max_torque, min(max_torque, float(torque)))
        alpha = -gravity / length * math.sin(theta) + torque / (mass * length**2)
        omega += alpha * dt
        theta += omega * dt
        # normalize to [-pi, pi]
        theta = math.atan2(math.sin(theta), math.cos(theta))
        thetas.append(theta**2)

    mean_sq = statistics.fmean(thetas)
    score = math.exp(-0.5 * mean_sq)
    return score, 0.0


def score_cartpole_pid(seed: int, code: str, params: dict[str, Any]) -> tuple[float, float]:
    """Cartpole balance with an adaptive-gain objective.

    Agent function signature: ``control(state, t, params) -> force``.
    State is ``(x, x_dot, theta, theta_dot)``.  Score is fraction of time
    the pole stays near upright and the cart stays on track.
    """
    mass_cart = float(params.get("mass_cart", 1.0))
    mass_pole = float(params.get("mass_pole", 0.1))
    length = float(params.get("length", 0.5))
    gravity = float(params.get("gravity", 9.81))
    dt = float(params.get("dt", 0.02))
    total_time = float(params.get("total_time", 4.0))
    timeout = float(params.get("timeout", 5.0))
    angle_limit = float(params.get("angle_limit", 0.2))
    position_limit = float(params.get("position_limit", 2.4))
    steps = int(total_time / dt)

    x, x_dot = 0.0, 0.0
    theta, theta_dot = 0.05, 0.0
    upright_steps = 0
    namespace = compile_agent_code(code, seed)

    for i in range(steps):
        t = i * dt
        force = call_agent_function(
            namespace, "control", ((x, x_dot, theta, theta_dot), t, params), timeout=timeout
        )
        force = float(force)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        total_mass = mass_cart + mass_pole
        temp = (force + mass_pole * length * theta_dot**2 * sin_t) / total_mass
        theta_ddot = (gravity * sin_t - cos_t * temp) / (
            length * (4.0 / 3.0 - mass_pole * cos_t**2 / total_mass)
        )
        x_ddot = temp - mass_pole * length * theta_ddot * cos_t / total_mass

        x_dot += x_ddot * dt
        x += x_dot * dt
        theta_dot += theta_ddot * dt
        theta += theta_dot * dt

        if abs(theta) < angle_limit and abs(x) < position_limit:
            upright_steps += 1

    return upright_steps / steps, 0.0


def score_lunar_lander(seed: int, code: str, params: dict[str, Any]) -> tuple[float, float]:
    """1-D lunar lander with engine failure at a fixed time.

    Agent function signature: ``control(state, t, params) -> thrust``.
    State is ``(altitude, velocity)``.  The main thruster loses 70% of its
    authority at ``engine_failure_t``.  Score rewards soft landing and fuel
    efficiency.
    """
    gravity = float(params.get("gravity", 1.62))
    max_thrust = float(params.get("max_thrust", 20.0))
    engine_failure_t = float(params.get("engine_failure_t", 3.0))
    failure_factor = float(params.get("failure_factor", 0.3))
    dt = float(params.get("dt", 0.05))
    total_time = float(params.get("total_time", 10.0))
    safe_vy = float(params.get("safe_vy", 2.0))
    timeout = float(params.get("timeout", 5.0))
    steps = int(total_time / dt)

    alt = float(params.get("initial_altitude", 100.0))
    vel = float(params.get("initial_velocity", -5.0))
    fuel = 0.0
    namespace = compile_agent_code(code, seed)

    for i in range(steps):
        t = i * dt
        if t >= engine_failure_t:
            available = max_thrust * failure_factor
        else:
            available = max_thrust
        thrust = call_agent_function(namespace, "control", ((alt, vel), t, params), timeout=timeout)
        thrust = max(0.0, min(float(thrust), available))
        fuel += thrust * dt
        vel += (thrust - gravity) * dt
        alt += vel * dt
        if alt <= 0.0:
            alt = 0.0
            break

    soft_landing = abs(vel) < safe_vy
    max_fuel = max_thrust * total_time
    efficiency = 1.0 - min(1.0, fuel / max_fuel)
    score = efficiency if soft_landing else 0.0
    return score, 0.0


def score_plc_anomaly(seed: int, code: str, params: dict[str, Any]) -> tuple[float, float]:
    """Text-only PLC anomaly detection.

    Agent function signature: ``detect(log_lines) -> list[int]``.
    The synthetic log contains a sinusoidal normal pattern with a few seeded
    anomalies.  Score is F1 against the ground-truth anomaly indices.
    """
    n_lines = int(params.get("n_lines", 120))
    n_anomalies = int(params.get("n_anomalies", 5))
    spike_magnitude = float(params.get("spike_magnitude", 8.0))
    noise_sigma = float(params.get("noise_sigma", 0.3))
    timeout = float(params.get("timeout", 5.0))

    rng = random.Random(seed)
    log: list[float] = []
    anomaly_indices: set[int] = set()
    # inject anomalies at deterministic offsets seeded by the task seed
    anomaly_starts = sorted(rng.sample(range(10, n_lines - 10), n_anomalies))

    for i in range(n_lines):
        base = 10.0 + 5.0 * math.sin(i / 10.0)
        noise = rng.gauss(0.0, noise_sigma)
        value = base + noise
        if i in anomaly_starts:
            kind = rng.choice(["spike", "flatline", "break"])
            if kind == "spike":
                value += spike_magnitude
            elif kind == "flatline":
                value = log[-1] if log else base
            else:
                value += spike_magnitude * 0.5
            anomaly_indices.add(i)
        log.append(value)

    namespace = compile_agent_code(code, seed)
    detected = call_agent_function(namespace, "detect", (log,), timeout=timeout)
    detected_set = set(detected) if isinstance(detected, (list, tuple, set)) else set()

    tp = len(detected_set & anomaly_indices)
    fp = len(detected_set - anomaly_indices)
    fn = len(anomaly_indices - detected_set)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return f1, 0.0


# Map used by the task loader/runner to validate and dispatch scoring functions.
SCORING_FNS: dict[str, ScoringFn] = {
    "score_quadrotor_altitude": score_quadrotor_altitude,
    "score_pendulum_swingup": score_pendulum_swingup,
    "score_cartpole_pid": score_cartpole_pid,
    "score_lunar_lander": score_lunar_lander,
    "score_plc_anomaly": score_plc_anomaly,
}


def get_scoring_fn(name: str) -> ScoringFn:
    """Return the scoring function registered under ``name``.

    Raises :class:`ValueError` if the name is unknown.
    """
    if name not in SCORING_FNS:
        raise ValueError(f"unknown scoring function {name!r}")
    return SCORING_FNS[name]


# Deterministic stubs used by the synthetic backend in CI.
# Baseline is intentionally naive; true_know applies a simple-but-effective
# controller so the harness sanity-check shows treatment > control.
TASK_STUBS: dict[str, dict[str, str]] = {
    "quadrotor_altitude": {
        "baseline": """def control(state, t, params):
    return params['mass'] * params['gravity']
""",
        "true_know": """def control(state, t, params):
    alt, vel = state
    target = params['target_altitude']
    return params['mass'] * params['gravity'] + 4.0 * (target - alt) - 4.0 * vel
""",
    },
    "pendulum_swingup": {
        "baseline": """def control(state, t, params):
    return 0.0
""",
        "true_know": """def control(state, t, params):
    theta, omega = state
    energy = 0.5 * omega * omega + params['gravity'] / params['length'] * (math.cos(theta) - 1.0)
    if abs(theta) < 0.3 and abs(omega) < 0.5:
        return -2.0 * theta - 0.5 * omega
    return 1.0 if energy < 0 else -1.0
""",
    },
    "cartpole_pid": {
        "baseline": """def control(state, t, params):
    return 0.0
""",
        "true_know": """def control(state, t, params):
    x, x_dot, theta, theta_dot = state
    return 1.0 * x + 1.0 * x_dot + 20.0 * theta + 3.0 * theta_dot
""",
    },
    "lunar_lander": {
        "baseline": """def control(state, t, params):
    return 0.0
""",
        "true_know": """def control(state, t, params):
    alt, vel = state
    target_vy = -1.0
    if alt < 20.0:
        target_vy = -0.5
    return params['mass'] * params['gravity'] + 2.0 * (target_vy - vel)
""",
    },
    "plc_anomaly": {
        "baseline": """def detect(log_lines):
    return []
""",
        "true_know": """def detect(log_lines):
    out = []
    for i, v in enumerate(log_lines):
        if i == 0:
            continue
        expected = 10.0 + 5.0 * math.sin(i / 10.0)
        if abs(v - expected) > 3.0:
            out.append(i)
    return out
""",
    },
}


__all__ = [
    "SCORING_FNS",
    "TASK_STUBS",
    "get_scoring_fn",
    "score_cartpole_pid",
    "score_lunar_lander",
    "score_pendulum_swingup",
    "score_plc_anomaly",
    "score_quadrotor_altitude",
]
