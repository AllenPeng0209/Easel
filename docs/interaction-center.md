# 互动中心

互动中心把一个客户的评论、私信、知识库、回复草稿、审核和处理回执放在同一个工作区。当前用户未提供官方接口权限，本轮使用明确标记的模拟消息验收；模拟收发不会请求小红书或抖音。

## 页面与状态

在本机 Easel `http://127.0.0.1:7860/` 点击侧栏“互动中心”，先选择客户，再查看收件箱。主要状态为待处理、待审核、已处理；发送失败或结果待核对在具体会话中显示，不另加一排状态卡片。

会话保留入站消息、草稿来源、修改后的文本、版本、人工接管状态、审核记录和发送回执。生成建议只使用当前客户的知识库；无匹配答案时转人工。外部聊天 Agent 还可通过 Skill 与 CLI 撰写草稿，这与系统内部知识库匹配是不同来源。

客户工作区的 `client_id` 约束记录归属，不能使用另一客户的会话或草稿 ID 发消息。当前整体 Easel 仍是本机应用，这种数据分区不等于完成了面向公网 SaaS 的租户认证和人员权限系统。

先选 Easel 顶层项目，再选互动中心客户。CLI 默认使用原有 `default` 项目；非默认项目为实际返回的 16 位小写十六进制 ID，每条命令都加全局参数 `--project-id PROJECT_ID`。它路由到 `/api/projects/PROJECT_ID/interactions/...`，与默认 `/api/interactions/...` 分开；项目 ID、客户 ID、平台渠道 ID 不能混为一谈。

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

## 验证

```bash
.venv/bin/python -m pytest tests/test_interaction_center_cli.py -q
.venv/bin/python scripts/validate_skills.py
.venv/bin/python scripts/validate_skill_commands.py
```

这些命令验证 CLI、Skill 和文档契约，不代表任何真实账号已经具备平台权限或完成收发验收。
