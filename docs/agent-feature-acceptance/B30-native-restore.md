# B30 / C20 · 原生工具跨进程恢复

日期：2026-11-03。状态：修复、真实适配器恢复及完整后端回归已通过；本记录随独立功能提交。

## 问题与行为

B30初版用进程内 `_easel_publish_thread_id` 判断工具是否已注册。CLI连接或服务重建后缓存消失，即使原生线程已经持久保存工具也被错误声明为不可用。

现在工具定义是否存在交给原生SDK持久状态及实际路由；响芽只依据当前Host callback是否启用来开放该通道，并保持thread/turn/name/namespace/callId/schema检查。新线程仍在start注册，resume不追加dynamicTools、不换native ID。原生清单中确实从未注册的旧线程不能假装具备工具；保留其历史并提示显式新会话/迁移。

工具可用不代表业务批准：恢复后的每次请求仍走原有actor/IP、账号、固定内容版本、人工决定和幂等操作。没有增加授权账本或用模型文字替代发布许可。

## 来源

复用Codex原生持久工具恢复协议，并保持已迁移DeepSeek Harness的Host/session生命周期边界；不是上游Python代码逐字复制。[官方App Server文档](https://developers.openai.com/codex/app-server)说明已注册dynamicTools持久于rollout，resume可沿用；本机0.166.0-alpha.5.1独立进程实证与之相符。

## 证据

- 原生协议审计：`.scratch/b30/native-restore-audit.md`、`native-restore-probe-results.json`。仅两条自有QA模拟会话，新的CLI进程分别收到easel_ask_user和easel_publish，ID不变，resume不带新定义/path/history，发布回调固定拒绝。
- 当前真实响芽适配器：`.scratch/native-restore/adapter_probe.py`、`adapter-proof.json`。独立新进程恢复同一QA native ID，恰好一次合法Host callback、返回rejected/no dispatch、模型完成AUDIT_DONE。shell/apps/browser/MCP/web search禁用，未加载发布器或社媒账号浏览器。
- 真stub-RPC双子进程回归先红后绿；相关组69项通过，相邻present/question/权限/workspace回归215项通过，独立review相关174项通过。
- 初次scratch适配器探针把AUDIT_DONE当单个token检查，因正常流式拆分而断言失败；改为累加tokens后通过，实际应用代码没有该故障。

## 最终验证

候选代码树 `01ebf4822488728dc2b3cdc18cbb19a8d4d83e56`，`.scratch/native-restore/review-tree`。Root完整执行 `.venv/bin/python -m pytest tests -q --tb=short`，exit0，2447 passed/17 skipped/2项既有弃用warning；日志`python-final.log`。两文件编译与feature-index差异检查通过。

当前真实适配器探针exit0，新进程PID52148恢复原QA native ID，Host callback恰好1次，rejected/no dispatch，模型流式完成AUDIT_DONE。wire记录证明没有thread/start、没有向resume追加dynamicTools/path/history；业务发布器未加载。首轮探针仅因AUDIT_DONE分token断言失误失败，累加后通过，应用本身无该错误。

前端与基线11845ad逐字节相同；沿用该基线440项前端测试、构建/lint通过的证据，不冒称本次修改了界面。此修复不关闭真实平台身份/回执、执行进程恢复或其余144项/AUT要求。
