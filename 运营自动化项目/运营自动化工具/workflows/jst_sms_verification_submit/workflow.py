"""聚水潭短信验证码提交 workflow 定义。

Hermes 收到用户飞书回复的 4 位验证码后调用本 workflow，把验证码填入当前 9222
Chrome 里的聚水潭短信验证码弹窗并提交。可选先复用「聚水潭揽收监控」尝试触发弹窗。

平台动作（连 9222 / Selector / 填码 / 提交）全在 Ops-Cli；本层只做参数校验、
按需触发、调用 Ops-Cli、收集结果，且绝不记录验证码明文（只记 masked_code）。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_sms_verification_submit import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_sms_verification_submit",
        "聚水潭短信验证码提交",
        [
            step("check_inputs", "校验参数", steps.check_inputs),
            step("find_trigger_workflow", "查找触发 workflow", steps.find_trigger_workflow),
            step("detect_sms_dialog", "检测验证码弹窗", steps.detect_sms_dialog),
            step("trigger_with_pickup_watch_if_needed", "按需触发弹窗", steps.trigger_with_pickup_watch_if_needed),
            step("submit_sms_code", "填写并提交验证码", steps.submit_sms_code),
            step("verify_session_restored", "确认验证通过", steps.verify_session_restored),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
