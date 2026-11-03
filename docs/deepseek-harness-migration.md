# DeepSeek Harness → Easel 迁移记录

日期：2026-11-03。产品清单见 [agent-workbench-backlog.md](agent-workbench-backlog.md)。本文区分“上游已下载”“源代码已复用”“接口已适配”“本机已验收”，四者不能互相替代。

当前功能：主包07/D30结构化发布回执核心随本次提交。复用固定DeepSeek WorkflowRunPanel展开生命周期及现有DisclosureRow，并适配Easel既有发布脚本、批准账本和Codex状态工具。独立候选树与隔离浏览器/Native证据见 [D30.md](agent-feature-acceptance/D30.md)。真实平台ACK/身份/公开读回及进程恢复仍开放，原144项及AUT01–AUT08范围不变。

## 当前逐功能状态

| 条目 | 当前结论 | 验收/实现边界 |
|---|---|---|
| D01 | 已提交 `3086ca8` | 显式交付及真实文件下载已验收 |
| C19 | 并发初始化修复 `f5118d9` | C23分页等目录剩余要求仍待 |
| D08 | 固定版本核心 `97a2071` | 不扩大为全部批准/业务闭环 |
| B30 | 精确批准基础 `11845ad`；原生恢复 `2cc4272` | 账号/内容hash/版本绑定基础已有；真实平台整体未关闭 |
| D30 | 结构化回执核心随本次提交 | 机器回执协议、晚到观察、只读Native查询及聊天卡已接线；真实平台成功另验收 |

对外进度仅按原01–10产品包汇报；M00–M12是内部模块，一包可多个功能commit。以下基线/早期验证保留为历史，不能关闭全部144项或AUT要求。Python持久化、权限、回执判定和SDK路由属于必要Easel适配，不冒称上游逐字复制。

## 已固定的上游来源

| 项目 | 核验值 |
|---|---|
| 官方仓库 | https://github.com/deepseek-ai/deepseek-harness.git |
| 本地完整 clone | `integrations/deepseek-harness/upstream` |
| revision | `ce7073d51254c26cd63b22a60835b8f948deb447` |
| package version | `0.2.2-alpha.2` |
| 许可证 | MIT，`Copyright (c) 2026 DeepSeek`；原文保留于 `integrations/deepseek-harness/LICENSE` |
| 来源清单 | `integrations/deepseek-harness/upstream-manifest.json` |
| 生产迁移目录 | `web/frontend/src/vendor/deepseek-harness/{composer,message,navigation}` |
| 版本策略 | 完整 clone 已忽略入库；真正迁移的源码、署名、适配和测试进入 Easel 版本管理 |

已在本机核对上述 clone 的 HEAD、origin 和 package version。清单中的 `selected_packages` 是候选上游包的逐文件 SHA-256 清单；`migrated_files` 只登记真正复用到产品中的源文件。候选清单不表示所有包都已导入、启用或通过验收。

## 复用与适配边界

用户要求优先 clone 和迁移成熟实现。执行策略是保留上游结构与行为，迁移可独立复用的 React／TypeScript／CSS，再为 Easel 的数据契约提供薄适配。不能把“参考过源码后重新写了一套”登记成原样复用，也不能因为下载整个 monorepo 就宣称迁移完成。

| 层 | 优先复用来源 | Easel 中的职责 | 允许的必要适配 | 首批状态 |
|---|---|---|---|---|
| 输入、附件、草稿 | `packages/client/ui-conversation`、`ui-attachment`、`ui-input-trigger` | `vendor/deepseek-harness/composer` 与 ChatPage | 将上传和引用接入授权素材 API；存储键加入 user/IP/session；去除与现有 Host 不兼容的全局依赖 | 本轮基础迁移/冻结回归已验证；剩余范围见清单 |
| 消息、工具、问答卡 | `ui-primitives`、`ui-tool`、`ui-user-questions`、`ui-approval` | `vendor/deepseek-harness/message` 与 MessageBubble／QuestionCards | 本地化、主题 token、工具事件与现有媒体画廊映射；保留未知工具回退与安全消毒 | 本轮基础迁移/冻结回归已验证；剩余范围见清单 |
| 阅读跟随和会话导航 | `ui-chat` 的 reading/scroll 实现、`ui-workspace` | `vendor/deepseek-harness/navigation` 与 ChatPage／Sidebar | 替换 Host 导航为 Easel 会话ID；与现有页面滚动容器对接 | 本轮基础迁移/冻结回归已验证；剩余范围见清单 |
| 引擎事件 | `ui-tool`／`ui-trajectory` 的身份和生命周期契约 | Codex／ACP → Python SSE → App消息状态 | Python引擎协议无法直接导入React包；必须保留 callId／parent、参数、输出与终态的对应关系 | 协议适配通过回归及本机Codex命令验收；非复制上游Python |
| 自主问答 | `ui-user-questions`、Inbox、Goal、Approval 契约 | 服务端默认决策、截止时间、持久问题记录；前端呈现和答复 | 默认策略、权限校验和竞态必须在服务器执行；不靠浏览器定时器假装自动推进 | 服务端默认/问答策略回归通过；AUT未全关闭 |
| 会话目录 | `session-controller` 持久会话头与投影契约 | 授权服务器目录、本地历史导入与重建 | 现有user/IP目录隔离；迁移旧localStorage；不丢弃原生历史边界 | 目录回归/本机刷新与重命名已验收；跨浏览器场景按清单 |
| 产物与业务卡 | `ui-deliverables`、`workspace-changes`、`ui-workflow-run`、右侧预览 | 复用交付视图，接选题／制作／审阅／发布实体 | 现有revision/hash、RBAC和真实回执不能被通用文件UI替代 | D01已提交3086ca8；D08版本store/UI切片已验收并随本记录独立提交；B30/D30业务批准与回执仍后续P0 |

上游 React 包依赖其 Cordis、Store、Host/preset 和 workspace 包体系；Easel 当前为 React 前端与 Python/FastAPI 后端。直接将插件入口 import 进来不会自动具备对应服务。对原样复制文件保留源路径与 SHA；对适配文件说明变动范围。不要为迁移删除 Easel 已有的 IP、平台账号、权限和工坊业务能力。

## 首批实施顺序（历史计划）

1. M00 固定源与许可证。已完成本地来源核验；不代表功能完成。
2. 并行 M01 结构化事件与 M02 附件、草稿。用户消息与真实工具状态先能可靠保留。
3. M03 自主默认策略、必要输入与答案记录；M04 服务器目录；M05 真正停止回执。
4. M09 的明确交付／版本身份与 M10 的业务确认。它们是下一批P0，本轮尚未实现；所有结果应可定位，不以自然语言代替回执。
5. M06 阅读与渲染、M11 会话导航。之后按依赖补 Queue/Steer、Plan/Subagent 与高级预览。

优先级详表保留 144 个原始 ID，并另列 8 项自主需求。每项关闭需要记录实现路径、测试命令/结果和实际界面或业务验收证据；当前文档中的“计划中”不自动随某个邻近功能通过而关闭。

## 自主推进的产品契约

| 情况 | 任务行为 | 用户看到的内容 | 服务端验收 |
|---|---|---|---|
| 已有足够信息 | 自主选择工具和后续步骤直到目标完成 | 当前步骤、产物和真实状态 | 不人为插入逐步确认；不会只生成计划就声称完成 |
| 普通偏好可缺省 | 可展示输入卡，等待策略窗口后使用推荐/画像/显式默认 | 问题、默认值、服务端截止时间、立即按默认继续 | 无人打开网页也按同一策略继续；默认不覆盖已给定的硬约束 |
| 用户在窗口内答复 | 接受有效答案，并取消同题默认决策 | 已接受答案记录，标注人工来源 | 与超时并发只产生一次决策；旧问题不能回答新任务 |
| 普通偏好超时 | 服务端记录采用默认并继续 | 采用了什么默认、来源和生效步骤 | 保存default来源；刷新可回放；前端不能仅关卡而后端仍挂起 |
| 缺少敏感凭据或必需权限 | 保持阻塞，继续可做的准备工作；明确必要输入 | 真实阻塞及输入/授权入口 | 不捏造凭据，不用推荐项代替身份验证 |
| 公开发布、提权或主体/版本变化 | 绑定精确动作、IP、账号和版本的确认 | 清楚批准对象与范围 | 超时不批准；版本/账号变更使原批准失效；双击无重复外部动作 |
| 短暂工具失败 | 有界、可核对恢复；检查外部动作回执后再决定重试 | 失败尝试和后续恢复保留 | 不重复发布；不可恢复则blocked，不无限重复同一失败 |
| 请求停止 | 先停止中，等后端终态 | 已停止／停止失败／未确认 | 只断SSE不等于实际停止；停后不自动启动下一步骤 |
| 达到目标 | 验证清单和产物，保存完成记录 | 完成项、交付版本、实际回执和残余限制 | 无文件/无回执不勾完成；媒体排队不等于成片生成 |

这里的默认答案只处理可合理缺省的信息。它不是对所有工具的全局自动批准，也不改变 owner/operator/viewer 的服务端权限。工作中可自主继续准备和制作，与公开执行具有外部影响的操作是不同阶段。

## 迁移记录格式

每个实际迁移文件在 `upstream-manifest.json` 的 `migrated_files` 登记：

```json
{
  "upstream_path": "packages/client/<package>/src/<file>",
  "upstream_commit": "ce7073d51254c26cd63b22a60835b8f948deb447",
  "destination_path": "web/frontend/src/vendor/deepseek-harness/<area>/<file>",
  "reuse_kind": "copied | adapted",
  "changes": "必要适配说明，原样复制则说明无语义改动",
  "verification": "实际执行的测试或构建证据"
}
```

某个实现只是参考上游交互或契约而由 Easel 适配，不使用 `copied`。如果它是业务专属代码，应登记到开发条目的实现证据，不混入上游版权声称。

## 不能混淆的官方能力条件

- 默认 Web 全文搜索关闭；metadata 搜索与正文索引分别验收。
- 内置 Browser 默认有 Desktop 条件，Web 需显式启用且仍受站点嵌入限制。
- Plan、子 Agent、Goal 依赖 Host/preset 的真实能力；注册 UI 不能证明执行端支持。
- Office/Excel 是预览，受本地转换服务/字体等条件影响；不等于可编辑、重算或可靠导出。
- `present` 是交付宣告；它本身不冻结所有历史文件内容，业务批准仍需绑定版本/hash。
- liveshare 是实验、非默认部署能力；Mermaid 本轮仍未验证默认渲染器。
- 通用 Harness 的 Workspace 不是商业 IP/RBAC；现有 Easel 业务隔离与发布回执保留。

## 验证记录

2026-11-03 文档层校验：原 144 个唯一 ID 全部一对一映射；保留原始优先级统计（P0 12、P1 73、P2 36、保持17、观察6）；新增 AUT01–AUT08 共8项独立计数。候选来源文件按固定提交记录 SHA-256，MIT 原文一致。运行功能验收由各实施模块另附证据，本文不声称全部144项完成。

## 基线实际迁移账本（冻结构建与回归已验证）

以下按磁盘实际文件登记，源与目标SHA在manifest中保存。verbatim表示去除新增来源头后原始正文逐字相同；adapted表示依赖、类型或独立组件边界有适配。

| 本地实际文件 | 上游文件 | 复用方式 | 必要改动 | 验证状态 |
|---|---|---|---|---|
| web/frontend/src/vendor/deepseek-harness/composer/ImageLightbox.module.css | packages/client/ui-primitives/src/ImageLightbox.module.css | adapted | 保留上游CSS主体；增加Easel语义颜色token映射和小屏padding。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/composer/ImageLightbox.tsx | packages/client/ui-primitives/src/ImageLightbox.tsx | adapted | 保留上游lightbox行为；仅将关闭图标替换为本地×字形并新增来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/composer/file-size.ts | packages/client/ui-primitives/src/file-size.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/composer/use-scroll-follow.ts | packages/client/ui-chat/src/client/chat/use-scroll-follow.ts | adapted | 保留ScrollFollowState及hook逻辑；把TypeScript构造参数属性展开为显式字段，以兼容当前Easel TS配置；新增MIT来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/DisclosureRow.module.css | packages/client/ui-primitives/src/DisclosureRow.module.css | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/DisclosureRow.tsx | packages/client/ui-primitives/src/DisclosureRow.tsx | adapted | 用Easel本地bridge替换clsx和图标依赖；为React 19 inert类型提供必要兼容；保留上游展开行结构。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/QuestionReplyBubble.tsx | packages/client/ui-user-questions/src/client/QuestionReplyView.tsx | adapted | 从QuestionReplyView提取独立props组件；移除Cordis/Host绑定，改用现有图标和原生title；保留问答上下排版与复制行为。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/QuestionReplyView.module.css | packages/client/ui-user-questions/src/client/QuestionReplyView.module.css | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/TextShimmer.module.css | packages/client/ui-primitives/src/TextShimmer.module.css | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/TextShimmer.tsx | packages/client/ui-primitives/src/TextShimmer.tsx | adapted | 用本地cx bridge替换clsx；保留上游闪烁状态与装饰文本逻辑。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/UserMessageBubble.module.css | packages/client/ui-primitives/src/UserMessageBubble.module.css | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/UserMessageBubble.tsx | packages/client/ui-primitives/src/UserMessageBubble.tsx | adapted | 用本地cx bridge替换clsx；保留上游消息气泡结构。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/clipboard.ts | packages/client/ui-primitives/src/clipboard.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/file-size.ts | packages/client/ui-primitives/src/file-size.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/question-drafts.ts | packages/client/ui-user-questions/src/client/draft-store.ts | adapted | 迁移draft-store存储生命周期；改用Easel user/IP范围storage桥接，收束本地问题/草稿类型。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/question-reply.ts | packages/client/ui-user-questions/src/client/question-reply.ts | adapted | 提取原有payload类型和问答复制函数；移除Cordis注册，将translator收束为本地函数边界。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/message/use-clipboard-copy.ts | packages/client/ui-primitives/src/use-clipboard-copy.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/navigation/focus.ts | packages/client/ui-primitives/src/focus.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/navigation/keyboard-composition.ts | packages/client/ui-primitives/src/keyboard-composition.ts | verbatim | 原始实现正文保持逐字一致；仅添加MIT、commit与来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/navigation/markdown.ts | packages/session-query/session-log-export/src/markdown.ts | adapted | 仅以本地结构类型替换workspace包类型导入；保留原Markdown转换实现；新增MIT来源头。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/navigation/order.ts | packages/client/ui-workspace/src/client/tree.ts | adapted | 从tree.ts抽取orderByRecency/pinCurrentBlank/sectionMembers纯函数；补本地最小元数据类型，非整文件原样复制。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |
| web/frontend/src/vendor/deepseek-harness/navigation/useModalLayer.ts | packages/client/ui-primitives/src/useModalLayer.ts | adapted | 保留上游模态键盘和焦点生命周期；移除相对导入.ts扩展名。 | 来源/SHA、冻结构建与回归已验证；功能范围见最终证据 |

基线实际迁移文件 22 个，其中原始正文逐字复用 10 个、适配迁移 12 个。另有 14 个本地集成适配文件单列，不混入原样复用统计。根代理最终全量回归与限定本机链路验收已通过；不能据此关闭全部144项或宣称M09/M10业务闭环已完成。

## 基线冻结验证结论（历史）

根代理报告：后端2032 passed/17 skipped（2项既有deprecation warnings）、前端356 passed/0 failed，构建/lint/diff-check exit0；主chunk约636KB为既有构建提示。实际IAB已登录Codex工具执行 `printf EASEL_AGENT_SMOKE_OK`，工具卡保留command/output/exitCode0；运行中队列消息随后自动执行，刷新恢复两轮与工具记录。桌面1280px轨道56px、447px手机抽屉开闭已实测，console error为空。

上述证据与命令详见开发清单末尾以及manifest的`verification`。目标SHA已在根代理宣布代码冻结后刷新并逐项核验。整体回归和这条本机链路通过，不等于每个未完成的业务、引擎、权限或任务重启场景均已验收。

下一批P0：M09明确交付/版本、M10真实业务卡/发布回执，以及自主生命周期中尚缺的持久目标和pending question重启恢复。M11只关闭已迁移/已验证的导航基础，其余缺口继续保留。

根代理的完整验收说明见 [agent-workbench-verification.md](agent-workbench-verification.md)，包含本机搜索/重命名保持、截图、自动化日志位置和未验收边界。

## 基线原生默认问答补验（历史）

新建Codex会话已完成真实 `easel_ask_user` → 风格输入卡 → 无人填写 → 25.0秒默认专业 → 继续真实shell `printf EASEL_DEFAULT_CONTINUED` → 输出及exitCode0 → 最终汇报。默认历史、倒计时与快捷操作可见，截图为 `.scratch/agent-workbench-verification/input-default-complete.jpg`。

原生 `request_user_input` 在默认模式受门禁，因此必要Python适配为新的 `thread/start` 注册 eager动态工具，`item/tool/call` 对接现有问答，再返回contentItems/success。工具定义和响应经过本机schema校验（0 errors）及新增12项协议回归。它是Easel原生引擎桥接，不登记成上游Python逐字复制。

旧未注册的原生线程不能经 `thread/resume` 追加工具，需新建对话；旧ID与历史保留，不能声称旧线程已无缝具备卡片。最终全量后端2032 passed/17 skipped（2旧warning）、前端356 passed；最终build exit0（108 modules，index-BSJixOw6.js约636.21KB既有体积提示）、lint零warning、diff-check通过。M09/M10与持久目标/待答future重启等未完成范围保持不变。

## D01初始逐功能阶段（历史，已由3086ca8收口）

此段保留D01初期历史：基线commit为 `1a4c8b7c63fbdbf4ed5cb3b4dedb092009203e7b`，当时显式present/store/文件卡已落地，但HTML相对图片403未通过。后续已修复、真实验收并提交3086ca8；以下初期文件列表不表示当前仍未验收。D08保持独立开发和提交。每功能验收记录见 [D01.md](agent-feature-acceptance/D01.md)，通用模板见 [agent-feature-acceptance-template.md](agent-feature-acceptance-template.md)。

| D01初期迁移文件（历史列表） | 上游原路径 | 复用方式 | 当前状态 |
|---|---|---|---|
| web/frontend/src/vendor/deepseek-harness/deliverables/Deliverables.module.css | packages/client/ui-deliverables/src/client/Deliverables.module.css | verbatim | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/PresentRow.module.css | packages/client/ui-deliverables/src/client/PresentRow.module.css | verbatim | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/PresentRow.tsx | packages/client/ui-deliverables/src/client/PresentRow.tsx | adapted | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/PresentedFileCard.tsx | packages/client/ui-deliverables/src/client/PresentedFileCard.tsx | adapted | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/file-metadata.ts | packages/client/ui-primitives/src/FileTypeIcon.tsx | adapted | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/presented.ts | packages/client/ui-deliverables/src/presented.ts | adapted | 历史阶段：当时未验收，后续D01已提交3086ca8 |
| web/frontend/src/vendor/deepseek-harness/deliverables/turn-deliverables.ts | packages/client/ui-deliverables/src/client/turn-deliverables.ts | adapted | 历史阶段：当时未验收，后续D01已提交3086ca8 |

D01声明工具目前只在新Codex线程注册；旧未注册resume保留ID/历史且需新建，ACP未注册对应MCP/present工具，不能声称支持。初期阶段证据曾为67项后端、12项前端、真实3卡/文本/PNG；随后HTML修复与最终回归/真实验收已通过，D01已提交3086ca8。Python store和Host边界列为Easel适配，不声称上游Python原样复制。


## D01 验证更新（2026-11-03）

D01已在独立功能代码树通过2122项后端、378项前端与真实Codex/下载/PNG/HTML验收；HTML原403已由上游有限资源打包及受控related读取关闭。功能已独立提交3086ca8，完整记录见 `docs/agent-feature-acceptance/D01.md`。旧阶段WIP/403为历史证据。D08固定版本store/UI已独立验收，随本记录提交；批准版本联动仍不标完成。

## 历史 D08 收尾状态

D01已真实验收并提交 `3086ca8`；C19并发初始化修复已提交 `f5118d9`，C23分页仍待。D08固定版本store和UI切片已验收：2236后端、414前端、构建/lint及隔离7862真实V1/V2、覆盖/删源18次下载与HTML/ZIP通过，随本记录独立提交；批准版本联动尚未关闭。B30/D30的精确hash批准与真实发布回执联动仍为后续P0，144项持续逐项推进。


当前D08固定版本功能验收见 [D08.md](agent-feature-acceptance/D08.md)：2236后端通过/17跳过、414前端通过、构建/lint/编译/差异通过；18次真实浏览器下载hash、覆盖与删源后的HTML/ZIP版本保持通过。此验收覆盖固定交付切片，不关闭B30/D30精确版本批准或其余计划项。来源manifest登记新增HostReadStore与ReviewTab源码迁移及必要Python持久化适配。


## D30 结构化发布回执核心

复用固定上游 WorkflowRunPanel 的展开/完成/焦点生命周期及既有 DisclosureRow；来源SHA与30个代码/测试目标文件SHA已登记在manifest的D30条目。Easel本地适配负责私有机器回执、固定操作与批准关联、既有CLI边界、读取及持久化观察。退出码、日志文案和UI跳转都不能单独确认公开发布。详见[D30验收记录](agent-feature-acceptance/D30.md)；真实平台及取消恢复仍按主包07未过验收追踪。
