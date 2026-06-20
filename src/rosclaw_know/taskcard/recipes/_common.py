"""Shared recipe building blocks for humanoid kicking tasks."""
from __future__ import annotations

from typing import Any

SOCCER_SCENE_OBJECTS: list[tuple[str, str, dict[str, Any]]] = [
    ("ball", "soccer_ball", {"diameter": "0.22m", "movable": True}),
    ("target_zone", "goal_region", {"distance": "1.5m"}),
]

SOCCER_ENV_ASSUMPTIONS = [
    "地面平整",
    "没有人类站在踢球方向",
    "足球附近无易碎物",
]

SOCCER_SCENE_UNCERTAINTY: list[tuple[str, str, str]] = [
    ("ball_pose_uncertainty", "足球位置可能存在视觉估计误差", "±5cm"),
    ("friction_uncertainty", "地面摩擦系数未知", "medium"),
]

SOCCER_FAILURES: list[dict[str, Any]] = [
    {
        "id": "ball_not_detected",
        "category": "perception_failure",
        "applies_to": ["perceive_ball"],
        "description": "视觉系统无法检测到足球",
        "observable_signals": ["perception_confidence < 0.5", "ball_pose is null"],
        "likely_causes": ["光照不足", "足球被遮挡", "颜色/形状与训练分布不匹配"],
        "severity": "S1",
        "recovery": ["调整头部视角", "请求多视角观察", "调用 memory 检索相似场景"],
        "how_trigger": {"enabled": True, "query_hint": "ball detection failure visual perception"},
        "memory_write": {"enabled": True, "event_type": "perception_failure"},
    },
    {
        "id": "ball_pose_drift",
        "category": "perception_failure",
        "applies_to": ["perceive_ball", "align_for_kick"],
        "description": "足球位姿估计在接近或踢球前发生漂移",
        "observable_signals": ["ball_pose_delta > 0.05m", "tracking_confidence drops"],
        "likely_causes": ["相机标定误差", "机器人移动导致视角变化", "足球滚动"],
        "severity": "S2",
        "recovery": ["踢球前重新检测", "降低动作速度", "进入 touch-probe 或视觉伺服模式"],
        "how_trigger": {"enabled": True, "query_hint": "object pose drift before contact"},
        "memory_write": {"enabled": True, "event_type": "pose_uncertainty"},
    },
    {
        "id": "unstable_support",
        "category": "control_failure",
        "applies_to": ["align_for_kick", "execute_kick"],
        "description": "支撑脚和重心关系不稳定",
        "observable_signals": ["balance_margin < 0.2", "com_projection outside support polygon"],
        "likely_causes": ["站位过近或过远", "摆腿幅度过大", "地面摩擦不足"],
        "severity": "S3",
        "recovery": ["调整站位", "降低摆腿速度", "重新规划支撑脚位置"],
        "how_trigger": {"enabled": True, "query_hint": "humanoid support polygon balance instability"},
        "memory_write": {"enabled": True, "event_type": "balance_failure"},
    },
    {
        "id": "torque_limit_violation",
        "category": "safety_failure",
        "applies_to": ["execute_kick"],
        "description": "踢球腿或支撑腿关节力矩超过安全阈值",
        "observable_signals": ["joint_torque > torque_limit", "motor_current spike"],
        "likely_causes": ["踢球速度过高", "接触时机错误", "足部被球或地面卡住"],
        "severity": "S4",
        "recovery": ["立即停止当前踢球动作", "降低动作幅度", "进入安全站立模式"],
        "how_trigger": {"enabled": True, "expected_strategy": "SAFETY", "query_hint": "torque overflow humanoid leg swing"},
        "memory_write": {"enabled": True, "event_type": "safety_failure"},
    },
    {
        "id": "missed_ball",
        "category": "control_failure",
        "applies_to": ["execute_kick"],
        "description": "脚部轨迹没有与足球有效接触",
        "observable_signals": ["contact_event == false", "ball_displacement < 0.1m"],
        "likely_causes": ["足球位姿估计错误", "脚轨迹偏差", "站位距离不合适"],
        "severity": "S2",
        "recovery": ["重新估计 ball_pose", "调整 stance distance", "降低踢球动作速度并重试"],
        "how_trigger": {"enabled": True, "query_hint": "missed ball foot trajectory contact timing"},
        "memory_write": {"enabled": True, "event_type": "skill_failure"},
    },
    {
        "id": "fall_risk",
        "category": "safety_failure",
        "applies_to": ["approach_ball", "execute_kick", "recover_balance"],
        "description": "机器人出现跌倒风险",
        "observable_signals": ["imu_pitch_roll exceeds threshold", "fall_detector warning", "balance_margin < 0"],
        "likely_causes": ["步态不稳定", "踢球动作过猛", "地面摩擦不足"],
        "severity": "S4",
        "recovery": ["停止任务", "进入安全支撑", "请求人工确认"],
        "how_trigger": {"enabled": True, "expected_strategy": "SAFETY", "query_hint": "humanoid fall risk balance recovery"},
        "memory_write": {"enabled": True, "event_type": "safety_failure"},
    },
]

SOCCER_CONSTRAINTS: list[dict[str, Any]] = [
    {
        "id": "no_human_in_kick_direction",
        "type": "human_safety",
        "description": "踢球方向上不得有人",
        "applies_to": ["execute_kick"],
        "check": {"method": "scene_query", "expression": "human_distance_in_target_direction > 2.0m"},
        "violation_action": "STOP",
        "how_strategy": "SAFETY",
    },
    {
        "id": "no_full_power_kick_without_sandbox",
        "type": "validation_safety",
        "description": "未经 sandbox 验证不得执行大幅度踢球",
        "applies_to": ["execute_kick"],
        "check": {"method": "runtime_policy", "expression": "sandbox_passed == true or kick_power == low"},
        "violation_action": "BLOCK",
        "how_strategy": "ABSTAIN_OR_SAFETY",
    },
]

SOCCER_PRIORS: list[dict[str, Any]] = [
    {
        "id": "low_speed_first",
        "type": "safety_prior",
        "description": "首次测试必须低速踢球，不允许大幅摆腿",
        "applies_to": ["execute_kick"],
        "source": "curated",
        "confidence": 0.9,
    },
    {
        "id": "keep_support_polygon_stable",
        "type": "control_prior",
        "description": "踢球前必须保证支撑脚和重心关系稳定",
        "applies_to": ["align_for_kick", "execute_kick"],
        "source": "cognitive_wiki",
        "confidence": 0.82,
    },
    {
        "id": "prefer_sandbox_before_real",
        "type": "validation_prior",
        "description": "真实 G1 执行前必须先进行 sandbox / sim replay",
        "applies_to": ["approach_ball", "execute_kick"],
        "source": "rosclaw_policy",
        "confidence": 0.95,
    },
]

SOCCER_MEMORY_QUERIES: list[dict[str, Any]] = [
    {"id": "similar_g1_kick_tasks", "query": "Unitree G1 kick ball balance recovery foot trajectory", "intent": "retrieve_similar_episode", "top_k": 5},
    {"id": "previous_balance_failures", "query": "G1 fall risk unstable recovery foot swing", "intent": "retrieve_failure_episode", "top_k": 5},
]

SOCCER_HOW_TRIGGERS: list[dict[str, Any]] = [
    {"id": "perception_plateau", "when": {"subtask": "perceive_ball", "condition": "perception_confidence < 0.75 for 3 attempts"}, "query_hint": "ball detection failure pose uncertainty"},
    {"id": "balance_warning", "when": {"metric": "balance_margin", "condition": "< 0.2"}, "query_hint": "humanoid balance instability before kick"},
    {"id": "torque_violation", "when": {"metric": "torque_limit_violation", "condition": "true"}, "query_hint": "torque overflow leg swing humanoid"},
    {"id": "missed_ball", "when": {"metric": "contact_event", "condition": "false"}, "query_hint": "missed ball foot trajectory timing"},
]

SOCCER_AUTO_EXPERIMENTS: list[dict[str, Any]] = [
    {"id": "kick_speed_sweep", "description": "测试不同踢球脚速度上限", "variables": ["foot_swing_speed"], "values": ["low", "medium"], "safety_gate": True},
    {"id": "stance_distance_sweep", "description": "测试不同站位距离", "variables": ["distance_to_ball"], "values": ["0.25m", "0.30m", "0.35m"], "safety_gate": True},
]

SOCCER_PROHIBITED = [
    "full_power_kick_without_sandbox",
    "kick_toward_human",
    "disable_torque_limits",
]
