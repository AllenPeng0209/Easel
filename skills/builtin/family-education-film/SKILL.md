---
name: family-education-film
description: >-
  用家庭教育选题策略、真人剧情或老师口播生成竖屏教育带货成片，并将剧本、可编辑分镜和可播放版本交付到 响芽 视频工坊。适用于厌学、手机边界、亲子沟通短剧及系列成片。
layer: produce
---

# 家庭教育成片

1. 用 [家庭教育选题与转化](../family-education-strategy/SKILL.md) 确定分集简报。跟随当前 IP。片名相同也不能覆盖旧作品，确定稳定批次名和集号。
2. 结合 [AI 视频生成](../ai-video-gen/SKILL.md) 和 [AI 微短剧](../short-drama/SKILL.md) 选生产方式。已有完整老师口播原片时，用 [整片视频产线](../video-production/SKILL.md) 的分场、字幕和包装检查；有剧情和对白时沿用短剧逐镜流程。H3 Ref2VA 模式用真实接口上传已获准的参考、以稳定请求 ID 提交、轮询原任务、下载并验摘要。默认先验一个镜头，核对身份、对白和动作，再扩展同类镜头。可复用有权使用的原生生成镜头，记录来源和精确入出点。清单要区分新生成、旧生成镜头重剪、实拍和旁白。
3. 对照 [成片验收](references/acceptance.md) 编写镜头和验收记录。统一画幅与角色外观，围绕一件道具或动作组织每集。对白音轨优先，字幕逐句对时，保留安全边距。老师收束段优先实拍或授权口播；旁白或行动卡作为明确标记的替代形式。用 [视频工坊](../video-workshop/SKILL.md) 的原版工作台保留分镜剪辑段和生成收据。
4. 在当前 IP 的 `outputs/<批次>/` 保存一份 JSON 清单。路径相对于该 IP 的 `outputs`。`timeline` 优先给依序已剪好的片段，缺省时把已完成的成片作为单段可编辑素材。

```json
{
  "series": "家庭教育小劇場",
  "episodes": [{
    "id": "01",
    "title": "到點收手機，先談怎麼停",
    "brief": "明確的家庭場景、對白、轉折、行動建議與 CTA 狀態",
    "path": "family-series/01.mp4",
    "timeline": ["family-series/01-hook.mp4", "family-series/01-story.mp4", "family-series/01-close.mp4"]
  }]
}
```

5. 在支持的 Codex 对话里，用宿主工具 `easel_workshop_delivery`。传 `{"action":"plan","manifestPath":"family-series/series.json"}` 登记视频工坊项目；成片存在且检查通过后，将同一清单的 `action` 改为 `deliver` 渲染并收集同一批工程。可传 `episodes` 集号数组，先验首支再处理后续。若宿主未提供工具，由已授权的操作者在 响芽 根目录使用本机 CLI。操作者经受控环境变量 `EASEL_VIDEO_SESSION` 传入现有的本机登录凭据，避免贴进聊天或制作清单。

```bash
.venv/bin/python scripts/easel_video_delivery.py /absolute/path/series.json --project IP_ID --plan-only
.venv/bin/python scripts/easel_video_delivery.py /absolute/path/series.json --project IP_ID
```

6. 每集实测完整解码、时长、画幅、视频与音轨，检查首尾和剪接处、中文对白与字幕、CTA 事实。首支要验证画面、对白、剪接和工坊播放，再沿用已通过的做法推进后续。每集保留生成收据、剪辑区间和实际看片判定。再进入对应视频工坊项目，核对资产与分镜、原生渲染回执、成片版本和播放器实际播放。同一批重试沿用原工程和回执。排队、生成片段、交付收据和人工批准分别记录。

按集交付工坊链接、实际秒数、成片及 QA。真人老师/商品信息、生成质量和发布审批分别如实标记。
