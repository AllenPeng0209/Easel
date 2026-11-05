# 互动中心

互动中心把一个客户的评论、私信、知识库、回复草稿、审核和处理回执放在同一个工作区。当前用户未提供官方接口权限，本轮使用明确标记的模拟消息验收；模拟收发不会请求小红书或抖音。

## 页面与状态

在本机 响芽 `http://127.0.0.1:7860/` 点击侧栏“互动中心”，查看收件箱、回复规则与账号连接。多个客户时可从页头选择工作区。主要状态为待处理、待审核、已处理；发送失败或结果待核对在具体会话中显示，不另加一排状态卡片。

会话保留入站消息、草稿来源、修改后的文本、版本、人工接管状态、审核记录和发送回执。生成建议只使用当前客户的知识库；无匹配答案时转人工。外部聊天 Agent 还可通过 Skill 与 CLI 撰写草稿，这与系统内部知识库匹配是不同来源。

客户工作区的 `client_id` 约束记录归属，不能使用另一客户的会话或草稿 ID 发消息。当前整体 响芽 仍是本机应用，这种数据分区不等于完成了面向公网 SaaS 的租户认证和人员权限系统。

先选 响芽 顶层项目，再选互动中心客户。CLI 默认使用原有 `default` 项目；非默认项目为实际返回的 16 位小写十六进制 ID，每条命令都加全局参数 `--project-id PROJECT_ID`。它路由到 `/api/projects/PROJECT_ID/interactions/...`，与默认 `/api/interactions/...` 分开；项目 ID、客户 ID、平台渠道 ID 不能混为一谈。

## CLI 与对话 Agent

所有命令在项目根运行。CLI 只接受回环 HTTP 原点，不继承网络代理、不跟随重定向，不把密钥放进命令。可用 `EASEL_INTERACTIONS_BASE_URL` 或 `--base-url` 指定另一个本机端口。

```bash
.venv/bin/python scripts/interaction_center.py --help
.venv/bin/python scripts/interaction_center.py bootstrap
.venv/bin/python scripts/interaction_center.py create-client --name "家庭教育客户"
.venv/bin/python scripts/interaction_center.py bootstrap --client-id CLIENT_ID
.venv/bin/python scripts/interaction_center.py conversation CONVERSATION_ID --client-id CLIENT_ID
.venv/bin/python scripts/interaction_center.py --project-id 0123456789abcdef bootstrap --client-id CLIENT_ID
```

`CLIENT_ID`、会话与草稿 ID 均来自实际返回值，示例不是预先存在的账号。`bootstrap` 返回客户、渠道、会话列表、知识库、规则和能力状态；单条会话返回消息、草稿、处理记录及发送回执。

## 模拟完整处理流程

点击“载入模拟消息”，或执行以下命令，显式创建虚构验收数据：

```bash
.venv/bin/python scripts/interaction_center.py simulation --client-id CLIENT_ID
.venv/bin/python scripts/interaction_center.py generate-draft CONVERSATION_ID --client-id CLIENT_ID --expected-revision 1
.venv/bin/python scripts/interaction_center.py edit-draft DRAFT_ID --client-id CLIENT_ID --expected-revision 2 --text "你好，请告诉我你想了解的课程方向，具体安排由工作人员确认。" --source agent
.venv/bin/python scripts/interaction_center.py approve-draft DRAFT_ID --client-id CLIENT_ID --expected-revision 3
.venv/bin/python scripts/interaction_center.py send-draft CONVERSATION_ID --draft-id DRAFT_ID --client-id CLIENT_ID --expected-revision 4 --operation-id sample-reply-one
```

每次成功修改都会返回新的会话版本。上例的 1、2、3、4 仅作格式说明，应按真实响应填写。最后一条只显示输入计划，不读取消息或验证发送条件；当前模拟验收确认后加 `--exec`，CLI 才重读并核验目标后提交模拟发送。模拟回执没有向平台发消息。

`edit-draft --text-file /absolute/path/to/reply.txt` 可保存多行原文，不经过 shell 解释文本。默认记录 `source=agent`。界面手工输入的草稿与 Agent 文案分别保留来源；重新修改或收到新消息会使旧审批不能替新内容放行。

## 自动回复与人工接管

```bash
.venv/bin/python scripts/interaction_center.py knowledge-list --client-id CLIENT_ID
.venv/bin/python scripts/interaction_center.py knowledge-add --client-id CLIENT_ID --question "想了解课程" --keyword "课程介绍" --answer "请告诉我想了解的课程方向，具体安排由工作人员确认。" --auto-send
.venv/bin/python scripts/interaction_center.py set-rules --client-id CLIENT_ID --mode auto_faq --enabled
.venv/bin/python scripts/interaction_center.py automation-run --client-id CLIENT_ID
.venv/bin/python scripts/interaction_center.py rules --client-id CLIENT_ID
```

`automation-run` 默认只显示运行计划，加 `--exec` 才按当前规则处理。`manual` 为人工模式，`assist` 保留建议供审核，`auto_faq` 仅允许已启用且明确允许自动发送的知识库答案。请先在模拟渠道验收，再根据具体真实账号授权启用相应规则。

人工接管会抑制后续自动回复；投诉、退款等阻断关键词、知识库未覆盖的问题应留给人工处理。

```bash
.venv/bin/python scripts/interaction_center.py action CONVERSATION_ID --client-id CLIENT_ID --expected-revision 5 --action takeover
.venv/bin/python scripts/interaction_center.py action CONVERSATION_ID --client-id CLIENT_ID --expected-revision 6 --action resolve
.venv/bin/python scripts/interaction_center.py set-rules --client-id CLIENT_ID --mode assist --disabled
```

## 真实接入边界

渠道页面展示实际接入能力。小红书浏览器评论导入、抖音官方评论接口，以及私信通道有不同的权限和验证要求；没有权限、凭据或可验证发送回执时保持待接入或人工处理，不能把按钮点击成功当作平台发送成功。

真实渠道审核不接受 `review_kind=agent` 或 `simulated`。CLI 的 `approve-draft` 默认使用 `agent`，因此不会代替用户给真实消息签署人工审批。只有用户已批准当前具体回复时，才使用 `--review-kind human` 记录这次批准；本机该字段仍是操作声明，不是经过认证的真人身份。

稳定的 `operation_id` 绑定同一次发送。超时或状态未知时先查原会话与回执，不换编号自动重发；新消息、草稿内容或接收目标变化后重读并重新审核。平台凭据只在相应设置和服务端保存，不发送到对话或普通运行日志中。

## 原生互动界面与范围

2026-11-04 用户要求仅保留互动相关能力，并统一账号管理、互动中心及 响芽 原有主题的界面。当前界面以原生组件呈现，移除整套运营 iframe、多层功能导航及重复标题。

- 账号管理显示简洁账号列表；单个账号展开后查看能力及补充验证。统一「添加账号」使用 响芽 原有平台目录，保留全部 12 平台入口；折叠区显示全部平台状态及总数。抖音、小红书登录一次后自动检查内容后台与互动身份，取消用途选择；需要时在同一流程继续平台验证。共享私有版本化快照，不能只凭 Cookie 存在声称所有能力已验证，也不能由登录成功解除发送审核。
- 收件箱统一评论和私信，左侧搜索筛选，右侧查看上下文及回复。真实连接存在时默认隐藏示例会话，示例入口放在更多菜单内。
- 回复规则集中管理接待方式与常用答复。
- 账号连接选择已登录账号及作品或私信会话，支持刷新来源及同步消息。
- 配色使用 响芽 全局主题变量，覆盖日间与夜间模式。

API 和后台调度收敛到互动范围。旧运营工作台入口返回 410，作品采集、下载、素材及发布接口停止对外提供；每个 IP 的运行时启用互动调度。具体实现与来源见 `integrations/creatorhub/README.md`。

## 独立服务联调记录（历史）

| 顺序 | 开发内容 | 进入下一项前的验收 |
| --- | --- | --- |
| 1 | 抖音自有作品评论只读导入 | 用一个已授权账号和一个作品同步两次，评论不重复、能打开原作品、跨项目及客户不可读取，失败后保留最近成功时间 |
| 2 | 抖音、小红书私信只读收件箱 | 按账号和会话分页，入站与出站方向正确，断线补拉无遗漏、无重复 |
| 3 | 竞品与关键词监控 | 单独展示采集结果和任务状态，暂停或恢复后可核对新增记录，研究资料可送入选题 |
| 4 | 素材与报表 | 选中素材后入库并保留来源，同一素材重试不会重复入库，报表可按范围导出 |
| 5 | 内容日历与发布队列 | 到期只派发已审核版本，同一账号只有一套执行路径，结果不明可保留原操作供核对 |
| 6 | 审批后回评论或私信，再评估自动回复 | 逐平台验证权限、目标 ID、发送回执与断线重试，人工接管立即生效 |

### 第一期配置

在同一台机器上单独运行 CreatorHub，使用其「本账号管理」同步抖音作品及该作品的评论。选定已有权限的自有账号和作品。从 CreatorHub 本机 API 的 `/api/accounts?platform=douyin` 取得账号 `id`，再从 `/api/account-works?account_id=ACCOUNT_ID` 取得对应作品的 `id` 和抖音 `item_id`。响芽 的客户 ID 可在所选 IP 的互动中心 bootstrap 响应中查看。

把以下变量配置到 **响芽 服务进程环境**，将示例替换为实际值。非默认 IP 将 `DEFAULT` 换成实际项目 ID 的大写形式。如果 CreatorHub 使用其他回环端口，在 响芽 进程环境设置 `EASEL_CREATORHUB_PORT`。

```dotenv
EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT_CREATORHUB_CLIENT_ID=00000000000000000000000000000001
EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT_CREATORHUB_ACCOUNT_ID=7
EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT_CREATORHUB_WORK_ID=11
EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT_CREATORHUB_ITEM_ID=7412345678901234567
```

在 响芽「互动中心 → 渠道接入 → 新增渠道」选择「CreatorHub · 抖音评论只读」，填写服务端绑定前缀 `EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT`。点击「同步评论」导入该作品最近最多 200 条已缓存的文本评论，再到收件箱查看原作品链接。需要刷新平台数据时，先在 CreatorHub 同步该作品评论，再到 响芽 导入。更换作品时为新渠道设置新的绑定前缀。

渠道卡片的时间指 响芽 最后一次成功从 CreatorHub 读取缓存的时间。账号状态来自 CreatorHub 保存的记录。此渠道保留草稿与处理流程，真实回复在抖音原作品下处理。

## 验证

```bash
.venv/bin/python -m pytest tests/test_interaction_center_cli.py -q
.venv/bin/python scripts/validate_skills.py
.venv/bin/python scripts/validate_skill_commands.py
```

这些命令验证 CLI、Skill 和文档契约，不代表任何真实账号已经具备平台权限或完成收发验收。
