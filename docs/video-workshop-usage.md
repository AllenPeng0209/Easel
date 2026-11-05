# 原生视频工坊

默认入口是 AI 对话。用户交办任务，AI 调用制作 Skill，成果自动进入工坊；工坊用于查看资产、分镜、候选、成片和审核。人工核对固定成片、文案、实际账号后，点击一次“审核通过并发布”，后台执行发布并记录结果。

工坊列表保留原来的紧凑表格。进入项目后直接运行火宝原版工作台与制作后端，并与响芽的剧本、资产、稳定分镜、候选和成片双向同步。旧画布入口、绑定与版本依赖已移除。现有历史成片和审核记录继续可读，历史编辑器字段在读取时退出当前状态计算；原文件不会因升级被删除。

## 资产和镜头

项目的 `assets` 管理角色、场景、道具、图片、音视频及字幕；每次更新素材保存不可变版本。`shots` 保存稳定镜头 ID、提示词、时长、素材绑定、候选和当前选择。重做单镜头创建新候选，其他镜头和原有选片保留。候选、成片保存为独立副本并附 SHA256。

项目内使用火宝原版阶段导航：“原始内容”“AI 改写”“资产制作”“视频制作”“拼接导出”。视频制作保留原版任务列表、分镜描述/氛围/提示词、参考素材、视频预览与历史候选。可修改剧本/镜头提示词、预览和采用候选、将具体镜头意见交回 AI。根据已选镜头执行本机 FFmpeg 合成；已有完整剪辑、配音和字幕的成片可以直接交付。

内容修改使用 `expected_revision`。冲突返回 409，不用最新版本静默覆盖人工修改。生成候选未被采用时不改变当前选片；采用候选或修改制作内容后，原成片审核失效。

## AI 工具

- `easel_workshop_delivery` 读取当前 IP outputs 中的系列清单。`plan` 创建或继续视频工坊项目，`deliver` 登记已经存在的真实完整视频。`production` 可保存剧本/资产/分镜；`publication` 可准备发布文案与目标平台。
- `easel_video_generation` 由 Host 直接调用现有本机 PPU JSON 服务。参考文件在 Host 核验并冻结；保留生产计划额度、原任务身份和一次提交意图。结果不明时不重新生成。
- 多片计划收取的是镜头素材，不是整片。Skill 剪辑、字幕及质量检查完成后，再交付完整成片。
- 视频模型使用已有 `VIDEO_API_KEY`。运营方可配置 `H3_BASE_URL`、`H3_REF11_BASE_URL`、`PIKA_H3_BASE_URL`，默认分别为本机 8878、8881、8882。已有 Pika 队列服务时，优先复用 `.easel/pika-h3-api16/connection.json` 的专用 0600 连接与凭据（当前专用隧道端口为 18882）；该凭据不与普通视频 Key 混用。模型请求与凭据只在 Host 内执行。

清单结构与制作规范见 [video-workshop Skill](../skills/builtin/video-workshop/SKILL.md)。

## HTTP API

所有路由沿用当前用户与 IP 授权。默认 IP 使用 `/api/workshop`，其他 IP 使用 `/api/projects/:ip/workshop`。

| 操作 | 路由 |
|---|---|
| 列表 / 创建 | `GET /projects`、`POST /projects` |
| 项目 / 修改简报 | `GET /projects/:id`、`PATCH /projects/:id` |
| 保存剧本、资产与分镜 | `PUT /projects/:id/production` |
| 登记镜头候选 | `POST /projects/:id/shots/:shot/candidates` |
| 采用候选 | `POST /projects/:id/shots/:shot/select` |
| 保存定位意见 | `POST /projects/:id/comments`，可带 `shot_id` |
| 本机合成 / 观察 / 收取 | `POST /projects/:id/renders`、`GET /projects/:id/renders/:render`、`POST /projects/:id/renders/:render/collect` |
| 直接交付完整成片 | `POST /projects/:id/deliverables` |
| 准备固定成片与发布对象 | `POST /projects/:id/publish/prepare` |
| 审核并自动发布 | `POST /projects/:id/reviews`，人工通过时附准备结果的批准编号及摘要 |
| 归档 / 恢复 | `POST /projects/:id/archive` |

准备发布不会执行提交。发布批准保存在 Agent 可写目录之外；工坊内保存的显示字段不能自行授予发布权限。执行前再次核对实际账号、媒体和当前审核版本。重复审核沿用同一个已批准操作，不重复发帖。模拟通过与要求修改都不会触发发布。

发布状态区分正在执行、已提交、平台处理中、结果待核对、失败和确认公开。平台回执未确认时不能仅凭进程结束、视频上传成功或审核通过就标成已发布。

## CLI

`scripts/video_workshop.py --help` 提供原生项目、剧本/素材/分镜保存、成片交付和意见登记。使用现有登录会话环境变量 `EASEL_VIDEO_SESSION`，可用 `--project` 指定已授权 IP。CLI 不打印凭据，不提供旧编辑器的透传命令。审核发布优先在工坊核对完整对象后操作。

## 火宝源码与运行时

原版源码、固定提交、许可证和必要适配见 [接入说明](../integrations/huobao-drama/README.md)。依赖还原使用 `bash integrations/huobao-drama/install.sh`。原版生成与拼接结果会自动同步为当前工坊的可审核交付；“审核发布”返回宿主的固定成片批准界面。
