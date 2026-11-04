# Easel 内置互动适配

上游平台适配代码固定于 CreatorHub 快照 `6e67e3d7b664ddd70dd815ba81f39e5788e972d2`，来源与文件 SHA256 记录在 `easel/vendor/creatorhub/UPSTREAM.json`。小红书离线签名所需的 JavaScript 依赖随包提供。

## 当前范围

2026-11-04 根据用户要求收敛到评论、私信、回复规则和平台账号连接。账号管理与互动中心使用 Easel 原生 React 页面及全局主题。原运营工作台、嵌入页面、监控采集导航、下载、素材入库和定时发布入口已从本接入中移除。

互动中心包含收件箱、回复规则、账号连接三个标签。账号页以列表显示账号，管理操作按账号展开，全部平台登录状态折叠显示并标注平台总数。「添加账号」选择器使用 Easel 原有平台目录，保留 12 平台入口。抖音与小红书使用单一连接流程，自动检查主站与内容后台的账号身份，用户不再选择用途。平台需要额外验证时，同一流程继续提示。其他平台保留原登录能力，不宣称已具备互动接入。现有原生互动连接覆盖抖音和小红书；消息来源可选择本账号作品评论或私信会话。

统一连接由当前 IP 的 AccountConnections 协调，原生引擎与发布/数据脚本保留各自物理浏览器目录，通过 `.easel/account-sessions/` 内版本化、私有的完整 storage state 复用登录。两端身份一致才共享，能力分别标注已验证或待验证；发帖和回复继续遵守实际平台授权及现有审核。退出登录撤销快照代次、取消未完成登录并清理两端会话，旧回调不能恢复已退出的账号。执行器恢复完整快照后才导航，已撤销的快照禁止回退旧账号。

## 运行和权限

正常启动命令为 `easel web`。每个 IP 拥有独立数据库、浏览器目录、任务引擎及私信接收器。运行数据位于当前 IP 的 `.easel/operations/`。模块由 Easel 进程直接调用。

受控 API 为 `/api/operations/` 与 `/api/projects/<IP>/operations/`。只允许账号登录及校验、本人作品评论读取、私信读取和互动回复任务。旧 `/workbench` 返回 410，采集、下载、素材与发布 API 返回 404。身份和角色由 Easel 验证，互动写入继续绑定具体账号、请求、任务和用户审批。

运行时启用 `interaction_only`，后台轮询保留账号检查、评论监控和互动处理，停止作品/弹幕扫描、采集、自动评论发现和发布调度。已有数据仍在原存储位置。

当前浏览器会话连接支持将文本评论及私信导入审核收件箱，并保留平台消息 ID、方向与时间。平台发送依赖对应通道权限；只读连接提供复制回复的操作。

## 验证

```bash
.venv/bin/python -m pytest tests/test_engagement_scope.py tests/test_operations*.py tests/test_interaction*.py tests/test_access_control.py -q
node --test tests/frontend/interactionScope.test.mjs tests/frontend/interactionCenter.test.mjs tests/frontend/accountLogin.test.mjs
npm --prefix web/frontend run build
```

上游快照尚未声明项目许可证，对外分发前需核实授权。
