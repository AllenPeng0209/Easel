---
name: video-workshop
description: >-
  在响芽视频工坊中通过火宝原版工作台管理剧本、角色场景道具、分镜和镜头候选。通过 AI 对话调用制作技能，生成后自动交付工坊；人工审核当前成片与目标账号后自动发布。适用于整片制作、参考视频复刻、系列短剧、单镜头返工及交付审核。
layer: produce
---

# 视频工坊

主流程：用户在对话交办 → 调用合适的制作 Skill → 保存剧本、资产和分镜 → 生成并剪辑真实视频 → 自动交付视频工坊 → 人工审核 → 自动发布并回传平台状态。

## 原版工作台与对话共用项目

列表沿用响芽紧凑项目表，进入项目后直接运行火宝原版 Nuxt/Vue 工作台及 Hono 制作后端。阶段、资产管理、分镜任务、参考绑定、历史视频、重绘、批量生成与拼接均复用上游源码。原版后端与对话共用同一项目；不要创建平行工程。修改遇到双向版本冲突时先读取最新内容，保护人工编辑。

工作台选择“当前 AI 底座”时，原版 AI 按钮通过现有 Skill Host 完成任务。直接保存原版页面修改会同步回工坊。对话需修改已有工坊时，可写一个当前 IP 内容目录中的 JSON 清单，包含 `workshopId`、`expectedRevision` 和 `production`（`raw_content`、`script`、`assets`、`shots`），调用 `easel_workshop_delivery` 的 `action=update` 保存。使用 Native 稳定 ID，不要用火宝数据库的数字编号覆盖既有资产和镜头 ID。

## 制作与自动交付

1. 读取当前 IP 的创作要求、真实参考和已有工坊。沿用其项目编号和当前 revision，不另建平行工程，不要求用户提供内部编号。
2. 将系列清单写入 `outputs/<内容项目>/series.json`。调用原生工具 `easel_workshop_delivery`，`action=plan`、`manifestPath=<内容项目>/series.json`，创建或续接视频工坊项目。已有工坊时，在对应集保留工具返回的 `workshopId`。
3. 根据任务选择短剧、家庭教育、配音、字幕或剪辑 Skill。新动态视频调用 `easel_video_generation`；模型目录、冻结生产计划、一次提交和原任务收取均由 Host 执行。沿用已授权算力与生成范围；不能通过换镜头编号绕过已拒绝或用尽的额度。
4. `production` 保存可编辑的剧本、素材与稳定分镜编号。人物、场景、道具使用稳定 asset ID，分镜通过 `asset_ids` 绑定。参考图片、视频、音频须来自当前 IP 内容目录，不使用外部身份、密钥或远端路径。
   在生成计划中，每个 clip 显式填写已有分镜的 `shotId`，同镜头的备用候选也绑定这一 ID。不要把候选编号当成新分镜。将净版、原始视频登记为 `kind=video`，独立音轨为 `audio`，SRT/ASS/VTT 为 `subtitle`，剧本、逐镜表、制作清单、提示词和 QA 的 MD/TXT/JSON/CSV 文件为 `document`；这些真实文件在工坊“二修素材”中查看或下载。文档须为非空 UTF-8，单文件不超过 5 MB，JSON 须有效；不登记脚本、HTML 或空占位文件。字幕和制作表可以下载后二修，不代表原版工作台已有独立字幕时间线。
5. 保留成功镜头和所有候选。选片、剪辑和最终成片是不同阶段。先按用户要求完成剪辑、原声、字幕及配乐，实际观看、听音检查连续性与台词，再交付完整文件。不能把最后一个生成段当作整片，不能用静态图片轮播冒充新生成的视频。
6. `action=deliver` 将真实成片自动复制为不可变交付版本，并关联回同一工坊。重复执行原批次会继续已有交付，不重复登记相同当前版本。

清单示例（路径相对于当前 IP 的 outputs）：

```json
{
  "series": "家庭教育",
  "episodes": [{
    "id": "01",
    "title": "先听孩子说完",
    "brief": "45秒竖版家庭教育短剧，原生对白，后期白色细描边字幕",
    "aspectRatio": "9:16",
    "path": "倾听/final.mp4",
    "production": {
      "script": "已确认的完整拍摄剧本",
      "assets": [{"id":"mother","kind":"character","name":"母亲","description":"沿用本片母亲形象","path":"倾听/assets/mother.png"}],
      "shots": [{"id":"shot-01","title":"放下手机","prompt":"完整画面动作与原声台词","seconds":8,"asset_ids":["mother"]}]
    },
    "publication": {"platform":"douyin","title":"先听孩子说完","body":"本片发布文案","tags":"家庭教育","aiGenerated":true}
  }]
}
```

`plan` 时成片可以尚未存在；`deliver` 时必须有真实完整音视频。`production` 和 `publication` 都可以按真实任务提供。平台不明确时先交付待审核，用户可在工坊补充发布信息；不要猜账号。Host 会核对平台实际登录身份，并冻结成片、文案及目标账号供审核。

## 单镜头返工

用户从工坊发起返工时，对话带有 `shotId` 和已保存修改意见。先读取最新项目，只修改所选镜头。为新生成使用独立的候选任务编号；`prepare` 可通过 `shotId` 关联原分镜。保留其他镜头、旧候选和当前选片，不覆盖原文件。新增生成仍遵守既有授权与生产计划。收取后新候选自动归入原分镜；采用新候选后再生成新的完整成片。

检查已有候选并确定实际采用来源后，先读取最新工坊 revision，在当前 IP 内容目录写一份采用清单，再调用 `easel_workshop_delivery` 的 `action=select_candidate`，`manifestPath` 指向该文件：

```json
{"workshopId":"<真实工坊ID>","expectedRevision":7,"shotId":"shot-01","candidateId":"<该镜头候选ID>"}
```

将示例中的 ID 和版本替换为实际回执。`candidateId` 来自该分镜的 `candidates[].id`，不是供应商任务号或成片交付号；清单只接受这四个字段，不传集数、路径、身份或批准值。Host 会校验当前权限、工程版本、候选归属和文件 SHA，保留所有历史，只更新当前选片。版本冲突时读回再核对，不覆盖人工修改。采用仅选择素材来源，不新增生成，也不代表人工质量或发布批准；记录实际采用区间与后期裁切。选择变化后旧成片审核失效，须重新完成整片、`deliver` 并准备当前版本的发布审核。

### 将已剪辑片段导入原分镜

多个剪辑分镜 S 共用一个生成单元 G 时，生产计划用 `shotId` 指向其中一个已有 S，原始 G 候选保留在那里。不要把 S 列表改成 G 列表；`update`/`deliver` 的制作内容要包含完整保留的分镜和资产。先实际剪辑各 S 的文件，再逐个通过 `easel_workshop_delivery action=add_candidate` 导入。`manifestPath` 指向当前 IP 内容目录中的 JSON，严格包含以下字段：

```json
{"workshopId":"<真实工坊ID>","expectedRevision":7,"shotId":"shot-02","path":"倾听/edits/shot-02.mp4","sha256":"<剪辑文件实际SHA256>","requestId":"<新建一次并固定的32位小写hex>","sourceShotId":"shot-01","sourceCandidateId":"<原始G候选ID>","sourceSha256":"<原始G候选实际SHA256>"}
```

仅导入一个已有分镜；来源须是同工坊指定分镜中已登记的候选。Host 校验当前权限、内容版本、来源候选和实际文件 SHA，并对导入文件创建独立快照、校验快照 SHA 和视频。回执返回 `candidateId/path/sha256/sourceDeclaration`；新候选 `taskId=null`，不冒充模型生成任务。`sourceDeclaration.kind=declared_edit` 只记录剪辑来源声明，两份 SHA 不能自动证明像素裁切关系；另存 S↔G、源入出点、后期处理和逐片段实播检查记录。

`requestId` 是固定导入请求号，每次新导入只创建一次；Host 通过固定命名空间、工坊 ID 和请求号派生独立 `candidateId`，回执明确返回两种 ID，避免占用模型生成记录编号。结果未知时读回已知的 `candidateId`，或用完全相同的请求重试（包含原 `expectedRevision`）；已有回执会在重新校验来源和候选文件后返回，不重复登记。相同请求号改动目标、路径、SHA 或来源会被拒绝；新请求遇版本冲突须读回最新版本后再核对。清单不接受身份、生成任务号、审批、远程 URL 或额外字段，也不接受 `episodes` 筛选。

已有选片保持不变；空分镜会采用首个候选并产生新内容版本。需要替换当前选片时，逐片段检查后调用 `select_candidate`。原片与剪辑片并存、可回选；导入和采用都不是人工审批，选片变化后须重新导出完整成片并准备审核。

用户也可直接编辑镜头提示词、切换候选或修改剧本。任何影响当前成片的修改都会产生新内容版本，旧审核不能批准新版本。

## 审核与自动发布

- AI 可以执行质量检查，记录 `kind=simulated`；这不是人工发布批准。
- 交付清单带 `publication` 时，Host 自动准备具体成片、文案、实际账号和 AI 生成声明。用户在工坊核对后点击“审核通过并发布”，一次操作即批准并自动执行本次发布。
- 不再要求用户跳到发布中心重复确认；同一工坊成片也不要另外调用 `easel_publish` 形成第二条发布任务。
- 发布只使用获批的不可变成片。账号、文案、成片或内容版本变化时重新准备审核，不沿用旧批准。
- `submitted` 仅代表平台已接收提交；只有确证公开可见才是已发布。未知结果保留原操作，只查询，不自动重发。

维护与 API 用法见 [视频工坊使用说明](../../../docs/video-workshop-usage.md)。原生 CLI 为 [video_workshop.py](../../../scripts/video_workshop.py)，不再依赖外部编辑器服务。
