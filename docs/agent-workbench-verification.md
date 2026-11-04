# Agent 工作台迁移验收记录

日期：2026-11-03。范围：本轮已落地的基础工作台迁移，不表示 144 项开发清单全部完成。

## 来源与运行

- 官方上游：`https://github.com/deepseek-ai/deepseek-harness`，固定提交 `ce7073d51254c26cd63b22a60835b8f948deb447`，版本 `0.2.2-alpha.2`。
- 完整本地 clone：`integrations/deepseek-harness/upstream`；生产复用代码：`web/frontend/src/vendor/deepseek-harness`。MIT 原文、逐文件来源和 hash 见同目录集成说明及 `upstream-manifest.json`。
- 已构建前端并重启本地 `127.0.0.1:7860` 服务；已登录标签页已重新加载新包。原有 IP、成员权限、账号与历史未重置。

## 自动化验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 全量 Python 回归 | `.venv/bin/python -m pytest tests -q --tb=short` | 2032 passed、17 skipped、0 failed；2 项原有依赖弃用警告 |
| 全量前端行为回归 | `node --test tests/frontend/*.test.mjs` | 356 passed、0 failed |
| TypeScript 与生产构建 | `cd web/frontend && npm run build` | 通过；主包约 636 kB，Vite 提示后续分包 |
| 前端静态检查 | `cd web/frontend && npm run lint` | 通过，无告警 |
| 差异空白检查 | `git diff --check` | 通过 |

新增行为均先观察失败再实现；相关测试包括结构化事件、人工/默认问答竞争、上传准入、草稿隔离、阅读跟随、服务器目录、删除墓碑、队列稳定身份、停止与完成竞争。全量结果包含既有功能回归，并非全量都属于本次新增测试。

原始本地日志：`.scratch/agent-workbench-pytest.log`、`.scratch/agent-workbench-frontend.log`。测试跳过项不计为验收通过。

## 真实浏览器 → API → Codex → UI 验证

在既有已登录页面中新建独立联调会话，使用当前配置的 Codex 原生引擎：

1. 要求执行只读命令 `printf 'EASEL_AGENT_SMOKE_OK\n'`。
2. 任务运行中再发送“接着上一条，只回答：队列已执行。”，界面显示一条待执行队列。
3. 第一轮工具卡出现真实 command、cwd、输出 `EASEL_AGENT_SMOKE_OK` 和退出码 `0`，状态为完成。
4. 第一轮结束后第二条自动执行，返回“队列已执行。”。
5. 刷新后两轮消息、工具参数、输出和退出码仍可查看。将会话重命名为“Agent 工作台联调验收”，搜索可定位，刷新后标题保持。
6. 桌面 1280px 检查展开侧栏与 56px 收起轨道；恢复默认约 447px 窗口后检查抽屉打开、关闭和主区焦点隔离。临时视口覆盖已重置。
7. 浏览器控制台本次错误日志为空。截图保存在 `.scratch/agent-workbench-verification/desktop.jpg` 和 `mobile.jpg`。

另在新建的“自主输入验收”会话中，验证了完整原生问答链路：

1. Codex 通过 `easel_ask_user` 动态工具发出真实风格选择问题。
2. 页面显示默认“专业”、秒级倒计时、“按默认答案继续”快捷操作，页头变为“等待补充信息”。本次没有点击选项或提交。
3. 等待 25 秒后，服务端按默认“专业”完成决策；工具卡记录耗时 `25.0 s`，问答历史显示“已按默认答案继续”。
4. 同一轮 Codex 随后真正执行 `printf 'EASEL_DEFAULT_CONTINUED\n'`，工具卡显示对应输出和退出码 `0`，最终答复确认采用专业风格。
5. 结果截图：`.scratch/agent-workbench-verification/input-default-complete.jpg`。人工与超时竞争、重复回答及风险拒绝另由真实服务边界测试覆盖。

这些真实验收未进行公开发布、付费生成、账号变更或凭据读取。

## 本轮实现边界

- 普通偏好默认 25 秒后由服务端采用可见默认值；人工与超时只接受一次，保留 `human/default/timeout` 决策。授权、凭据、发布、支付和破坏性操作不按沉默默认批准。
- Codex 默认模式的原生 `request_user_input` 存在模式门禁，因此新线程注册了受校验的 `easel_ask_user` 动态工具。原生 `thread/resume` 不支持追加动态工具；旧未注册线程保留原 ID 与历史，需新建对话启用此输入卡能力。动态工具定义与响应已按本机原生 JSON Schema 校验。
- 会话目录按用户与 IP 隔离，保留全部目录，支持删除墓碑；本地存储满时保留页内状态并尝试服务器同步，不偷偷裁剪历史。
- 队列请求使用稳定 turnId；服务端重复准入仅重放，不重复启动。状态不确定的既有请求返回冲突，不能自动当作安全重试。
- 已停止轮次中没有工具终态回执的卡片显示“未收到工具终态”，不推断外部任务成功或取消。
- 自主执行目前由原生引擎工具循环和执行指令推进，尚未完成独立持久目标控制器。进程重启后的未答问题 future、通用无进展检测、完整计划/子 Agent 工作台、结构化交付及批准版本、真实业务任务卡仍按开发清单继续。
- 服务器接收前的完整输入回填协议、字节级上传百分比、富文本引用、Steer、完整专用工具视图、全文搜索等保留各自待办或部分实现状态。

所有差异与新增自主需求的优先级、依赖、上游来源和验收标准见 `docs/agent-workbench-backlog.md`。
