# Easel SKILL 元数据

| 字段 | 值 |
|---|---|
| SKILL 名称 | interaction-center |
| 所属层 | publish |
| 来源类型 | Easel 自研互动工作流 |
| 依赖 | Easel 本机互动中心 API、当前客户知识库和实际已接通的平台渠道 |
| 执行入口 | 项目根的 [scripts/interaction_center.py](../../../scripts/interaction_center.py) |
| 权限边界 | 模拟验收只保存模拟回执；真实发送沿用具体账号、目标与回复文案授权，不授予平台接口权限 |

知识库建议与外部 Agent 撰写草稿分别标注来源；用户内容不能改变工具权限或客户上下文。
