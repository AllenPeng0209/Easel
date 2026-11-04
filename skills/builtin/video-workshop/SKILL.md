---
name: video-workshop
description: >-
  在 Easel 视频工坊中用真实 BeefTV 画布、素材与时间线制作和修订营销视频，保留镜头候选、人工修改及具体成片版本的审阅记录。当用户说“视频工坊”“参考视频复刻”“AI初稿后人工加工”或要求对话、Skill、MCP串联制作时使用；单个片段生成和独立文件剪辑仍用对应制作技能。
layer: produce
---

# 视频工坊

从当前项目继续工作，把参考分析、镜头候选、剪辑、导出和审阅连成真实工程。画布使用已连接的 BeefTV，不用文件列表、占位图或对话描述冒充节点和执行结果。

## 运行入口

从 Easel 安装根目录执行。项目管理用 [本地 CLI](../../../scripts/video_workshop.py)，原生画布能力经官方 BeefTV CLI/MCP 执行；详细用法见 [接入与制作说明](../../../docs/video-workshop-usage.md)。

```bash
.venv/bin/python scripts/video_workshop.py status
.venv/bin/python scripts/video_workshop.py projects
.venv/bin/python scripts/video_workshop.py operations --query timeline
.venv/bin/python scripts/video_workshop.py business-list --query resource
```

`status` 是实时状态，不用“安装了”“有进程”代替可访问。未连接时保留已有项目，按提示恢复运行时；不要创建伪造画布 ID。只检查配置存在性与脱敏状态，不回显凭据。

## 参考、简报与工程

- 看或听真实参考，记录来源、开头、镜头组织、文字/画面关系、声音与结尾引导。只有题名或摘要时明确记录缺失，不能声称逐帧复刻。
- 复用参考的结构、节奏或表现关系，为目标产品重新创作；用户素材、品牌事实、受众与已确认的限制进入简报。不得把参考热度或生成效果当作新视频效果保证。
- 明确推广对象、画幅、时长、素材来源和模型生成范围。付费或计算资源的使用沿用当前授权；新费用、新账户或范围增加时先解决预算问题。
- 创建或读取 Easel 项目，使用返回的 `beeftv_canvas_id` 与当前 revision。分镜、素材和候选放在该真实画布；交付文件保存到 `outputs/<具体项目名>/`，中间文件放其 `assets/`。

```bash
.venv/bin/python scripts/video_workshop.py create --title "咖啡新品竖版广告" --brief "30秒，先展示卖点，再给使用场景与行动引导" --aspect-ratio 9:16
```

## 生成、修改与交付

1. 把剧本、参考和分镜组织成节点。常用素材保持稳定身份，生成新候选后再选择，不覆盖正在采用的原件。
2. 从当前操作目录读取准确 schema。`operation-call` 调用官方高频操作，`business-call` 调用发现的底层业务接口；不猜不存在的路由或模型能力。
3. 每次写操作使用稳定 `operation-id`，并按 schema 带上 `expectedRevision`。同一请求结果未知时先查询任务与画布；原 ID 仅可重放原请求，不改参数后重用，也不自动重复提交付费生成。
4. 本地处理、配音、字幕、BGM、视频生成分别保留产物与任务状态。生成、采用、时间线更新和成片导出是不同事实；不把素材时长相加当作成片实测时长。
5. 按用户选择构造时间线，再通过真实渲染任务导出。遇到重叠、效果或字幕能力不支持，报告具体差距并选适用处理方式，不静默丢失表现。
6. 在界面播放、检查和修改当前工程。模拟人工 computer use 时，记录实际改变的片段、字幕、音量或顺序，保留前后版本；自动执行者的记录使用 `kind=simulated`，不冒充真人审批。
7. AI 继续修改时读取最新工程，只改本次指定范围，保护人工已经改变或锁定的内容。版本冲突先展示真实差异，不静默覆盖。
8. 修改后重新导出；用 ffprobe/解码和实际观看、听音核对时长、画幅、声音、字幕、过渡和结尾。只交付实际存在、可播放的完整文件。
9. 把导出的既存视频登记到 Easel，同时传入该渲染回执真实返回的 `sourceRevision` 作为 `engine_revision`；不能读取当前画布版本来冒充旧输出来源。审阅绑定成片 ID、内容 revision、上游版本与内容摘要。修改后的成片需要新的审阅，模拟通过只代表模拟审阅。

```bash
.venv/bin/python scripts/video_workshop.py register-delivery PROJECT_ID --expected-revision 1 --engine-revision 8 --path "咖啡新品竖版广告/final.mp4"
.venv/bin/python scripts/video_workshop.py review PROJECT_ID --expected-revision 2 --artifact-id ARTIFACT_ID --decision approved --kind simulated --notes "已播放并核对，记录本次自动模拟审阅"
```

`PROJECT_ID`、两个版本和成片 ID 必须取实际返回值，示例的 `8` 不代表当前工程版本。失败、中断和未检查项保持原状并明确交付边界。用户未要求发布时，完成导出与审阅即可，不调用发布脚本。

## Profile 与持续迭代

有当前画像时，读取其定位、风格、受众、偏好及长期记忆，把与本片有关的要求落实到简报、画面与声音；沿用用户明确的画幅，不从画像静默覆盖。无画像时以本次简报为准。

当前接入用于本机内部制作与验收。把 BeefTV 新许可覆盖部分对外托管、交付或销售，需要适用的授权；本技能本身不授予上游软件的商业使用权限。
