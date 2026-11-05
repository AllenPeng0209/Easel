"""Typed, allowlisted conversation recipes.

Adapts DeepSeek Harness's leading command claim and editable suffix contract
(ui-conversation input/machine.ts, ce7073d, MIT) to ordinary Xiangya chat.
Recipes never dispatch tools directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    token: str = Field(min_length=2, max_length=65)


@dataclass(frozen=True)
class _Recipe:
    id: str
    token: str
    label: str
    group: str
    description: str
    argument_hint: str
    instruction: str

    def public(self) -> dict[str, str]:
        return {"id": self.id, "token": self.token, "label": self.label, "group": self.group,
                "description": self.description, "argumentHint": self.argument_hint}


_RECIPES = (
    _Recipe("social-draft", "/文案", "起草文案", "内容创作",
            "根据指定主题和受众起草社媒文案。", "主题、平台、语气或目标长度",
            "起草一版社媒文案。根据给定平台和受众组织标题、正文及可选话题，区分已知信息和创作假设。"),
    _Recipe("voice-script", "/口播", "口播脚本", "内容创作",
            "编写适合朗读的短视频脚本。", "主题、时长、受众和风格",
            "编写自然可读的口播脚本，按开场、重点和收尾组织。如果给了时长，按该时长控制篇幅。"),
    _Recipe("review-draft", "/审稿", "审阅草稿", "内容审阅",
            "检查已提供的草稿并给出修改建议。", "草稿、审阅重点或目标受众",
            "审阅本轮提供的草稿，指出事实、表达和平台适配问题，给出可执行的修改建议。缺少原文时说明待核对项。"),
)
_BY_ID = {recipe.id: recipe for recipe in _RECIPES}
# Match the browser editor's ECMAScript whitespace boundary exactly.
_SEPARATORS = (" \t\n\r\v\f\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff"
               + "".join(chr(codepoint) for codepoint in range(0x2000, 0x200B)))


def chat_command_catalog() -> list[dict[str, str]]:
    return [recipe.public() for recipe in _RECIPES]


def validate_chat_command(message: str, command: ChatCommand) -> tuple[_Recipe, str]:
    recipe = _BY_ID.get(command.id)
    if recipe is None or command.token != recipe.token:
        raise HTTPException(422, "对话命令已失效，请重新选择")
    if not message.startswith(recipe.token):
        raise HTTPException(422, "对话命令必须位于消息开头")
    suffix = message[len(recipe.token):]
    if suffix and suffix[0] not in _SEPARATORS:
        raise HTTPException(422, "命令与补充要求之间必须留有空白")
    return recipe, suffix.strip(_SEPARATORS)


def chat_command_message(message: str, command: ChatCommand) -> str:
    recipe, suffix = validate_chat_command(message, command)
    guidance = (f"〔对话操作：{recipe.label}〕\n{recipe.instruction}\n"
                "在当前对话内给出文字或建议。此配方不授权运行命令、修改文件、发布或对外提交。")
    if suffix:
        return f"{guidance}\n\n用户补充要求：\n{suffix}"
    return guidance
