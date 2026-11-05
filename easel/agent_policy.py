"""Xiangya policy adapter for DeepSeek Harness-style timed human input.

The attached question, timed continuation and recorded-answer separation follows
DeepSeek Harness ui-user-questions (MIT), commit
ce7073d51254c26cd63b22a60835b8f948deb447. The server timer and conservative business
approval policy adapt that lifecycle to Xiangya's Python/native-engine boundary.
See integrations/deepseek-harness/LICENSE for the upstream notice.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass


_RISK = re.compile(
    r"permission|approval|isSecret|执行授权|额外权限|授权确认|确认发布|立即发布|是否发布|"
    r"确认删除|删除(?:文件|账号|项目|内容|记录|全部|所有)|永久删除|是否删除|付款|支付|转账|"
    r"密码|密钥|凭证|API\s*Key|登录验证码|二次验证|清空|抹除|销毁|"
    r"\b(?:publish|delete|remove|erase|pay|payment|purchase|password|secret|token|credential|credentials|"
    r"authenticate|authorization|grant|login)\b|(?:允许|确认).{0,12}(?:发布|登录|授权)", re.I)
_DENY = re.compile(r"^(?:\d+[.)、:]\s*)?(?:拒绝|不允许|取消|不执行|稍后|暂不|deny|decline|reject|cancel|no\b)", re.I)
_ACTION_CHOICE = re.compile(r"发布|发出去|删除|授权|购买|支付|付款|转账|登录|清空|销毁")


@dataclass(frozen=True)
class QuestionPolicy:
    requires_human: bool
    default_answers: dict[str, list[str]]
    wait_seconds: float
    timeout_answers: dict[str, list[str]]


def _seconds(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        return min(maximum, max(0.01, value)) if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _label(option) -> str:
    return option if isinstance(option, str) else str(option.get("label", "")) if isinstance(option, dict) else ""


def public_questions(questions: list[dict]) -> list[dict]:
    """Publish only the question schema, never arbitrary model/RPC metadata."""
    output = []
    for item in questions:
        safe = {key: value for key, value in item.items()
                if key in {"questionId", "header", "question", "multiSelect"}}
        safe["options"] = [
            {key: value for key, value in option.items() if key in {"label", "description"}}
            if isinstance(option, dict) else {"label": option}
            for option in item.get("options", []) if isinstance(option, (str, dict))]
        output.append(safe)
    return output


def requests_secret(questions: list[dict]) -> bool:
    return any(item.get("isSecret") is True or re.search(
        r"(?:输入|提供|填写|粘贴).{0,20}(?:密码|密钥|凭证|验证码)|"
        r"(?:provide|enter|paste|input).{0,30}(?:password|secret|api\s*key|credential)",
        str(item.get("question", "")), re.I) for item in questions)


def question_policy(questions: list[dict], mode: str = "autonomous") -> QuestionPolicy:
    """Choose ordinary defaults; model-provided flags can never waive approval."""
    requires_human = mode != "autonomous" or any(
        item.get("requiresHuman") is True or item.get("isSecret") is True
        or item.get("kind") == "approval"
        or _RISK.search(" ".join(str(item.get(key, "")) for key in (
            "questionId", "header", "question", "options", "defaultAnswer", "defaultAnswers")))
        or _ACTION_CHOICE.search(str(item.get("options", "")) + str(item.get("defaultAnswer", ""))
                                 + str(item.get("defaultAnswers", "")))
        for item in questions)
    defaults: dict[str, list[str]] = {}
    safe_declines: dict[str, list[str]] = {}
    for item in questions:
        identifier = str(item.get("questionId") or item.get("id") or "")
        options = item.get("options") if isinstance(item.get("options"), list) else []
        labels = [_label(option) for option in options if _label(option)]
        if not identifier:
            continue
        decline = next((label for label in labels if _DENY.search(label)), None)
        if decline:
            safe_declines[identifier] = [decline]
        if requires_human:
            continue
        explicit = item.get("defaultAnswers", item.get("defaultAnswer"))
        values = explicit if isinstance(explicit, list) else [explicit] if isinstance(explicit, str) else []
        values = [value for value in values if isinstance(value, str) and value.strip() and (not labels or value in labels)]
        if not item.get("multiSelect"):
            values = values[:1]
        recommended = next((_label(option) for option in options
            if isinstance(option, dict) and (option.get("recommended") or option.get("isDefault")
               or re.search(r"推荐|recommended", _label(option), re.I))), None)
        defaults[identifier] = values or ([recommended or labels[0]] if labels else [
            "按当前 IP 画像与已知资料采用合理默认值继续完成任务；缺失事实明确标注，不得虚构事实。"])
    # A batch must be answered completely. Never manufacture a positive answer
    # for an approval with no unambiguous deny option.
    declines = safe_declines if len(safe_declines) == len(questions) else {}
    return QuestionPolicy(requires_human, defaults,
        _seconds("EASEL_AGENT_APPROVAL_WAIT_SECONDS", 300, 1800) if requires_human else
        _seconds("EASEL_AGENT_QUESTION_WAIT_SECONDS", 25, 300), declines)


def autonomy_instructions(mode: str = "autonomous") -> str:
    if mode != "autonomous":
        return "\n\n## 交互执行\n关键偏好不明确时可请求用户补充；授权始终遵守现有权限。"
    return (
        "\n\n## 自主推进与完成标准\n"
        "用户已要求你自主推进直至任务完整完成。先利用当前 IP 画像、已知资料和真实工具执行，"
        "普通风格、排版和可逆选择采用合理默认值，不要为了偏好反复打断。"
        "只有信息会实质影响正确性且无法从授权资料取得时，才请求结构化用户输入；"
        "普通问答会显示默认答案并在等待后由服务端继续，收到默认答案后继续执行。"
        "缺失事实必须标明待确认或采用可编辑占位，不得虚构事实、账号、授权或成功回执。"
        "执行、检查真实结果、修复可恢复错误后再交付，不要停在计划或声称完成。"
        "权限、秘密凭据、公开发布、永久删除及支付必须真实人工确认；无人确认时安全拒绝或标明受阻，"
        "可继续完成不依赖该动作的准备工作，不能把沉默视为批准。"
    )
