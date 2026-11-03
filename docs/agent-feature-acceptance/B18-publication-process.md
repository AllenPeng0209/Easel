# 主包05/B18 · 发布进程取消与账号锁终态

日期：2026-11-03。状态：本机进程生命周期修复通过独立候选树、真实本地进程及隔离界面验收，随本次单独功能提交。主包05/07与原144项、AUT范围仍按台账继续，不因本切片关闭整包。

## 问题与行为

原工作流取消等待 `asyncio.to_thread(subprocess.run)` 后会先记录unknown并释放账号锁，底层线程/发布进程却可能继续运行。Root用真实本地TERM-ignore进程复现：取消外层时profile_busy已经False，而进程仍存活；见 `.scratch/b18-process/workflow-red.log`。

现在固定argv发布进程由异步owner管理。取消、超时、正常直接子进程退出及运行观察异常都要完成同一清理屏障；POSIX先TERM、宽限后KILL，观察进程组非僵尸成员归零，并await回收直接子进程，再结束工作流。直接exit0、管道EOF或信号调用返回均不单独证明全部进程结束。输出双流有字节预算，保留截断事实。

取消过程中持久化executing/stopping，重复取消不能跳过清理。无法确认清理时保留executing/termination_unconfirmed和账号锁，界面显示“停止未确认，账号仍锁定”；不自动重发，也不继续使用旧短信提示。执行器确定未能启动时记录failed/notStarted。实际终止后仍读同一操作的机器回执，取消不抹除已取得的真实强回执；进程终止事实与平台发布事实分别保留，后续只读观察不能覆盖Host的终止事实。

## 来源与边界

固定DeepSeek Harness `ce7073d51254c26cd63b22a60835b8f948deb447`（MIT）：适配 `packages/subprocess/subprocess-local/src/managed-owner.ts` 的观察失败拒绝、whole-range settlement，及 `packages/shell/bash-local/src/index.ts` 的TERM→KILL、有界输出契约。Python实现与Easel批准/回执接线为必要Host适配，不宣称逐字复制TypeScript。来源与12个代码/测试文件SHA登记在manifest的B18-publication-process条目。

macOS实机验证的管理范围是本次新建POSIX进程组内的非僵尸成员；仅直接子进程可由owner wait回收，非直接后代僵尸由其父/init回收。主动setsid逃组仍需独立OS containment。Linux未做实机验收；Windows仅有taskkill及独立inventory的协议fixture验证，不当作Windows实机通过。

## 最终验证

基线 `07541153a20b915881bd7feb2e8b41875e26383b`；独立候选代码树 `982e247ee207e8a5d317620d1ff91e61573cdc79`，目录 `.scratch/b18-process/review-tree`。并行social-account/Sidebar改动未混入候选，也未被还原。

- 完整Python suite：**2760 passed / 17 skipped / 2项既有弃用warning**，exit0，`python-final.log`。
- 完整前端：**475 passed**，build/lint exit0；`frontend-final.log`、`build-final.log`、`lint-final.log`。构建产物 `.scratch/b18-process/dist`；只有外部输出目录提示及既有大chunk提示。
- 12文件候选编译/差异检查通过。最终提交与该候选代码的差异仅为验收/来源/台账文档。
- Runner 13项覆盖实际三代TERM-ignore子孙、超时、重复取消、root先exit0后仍有后代、spawn取消竞态、普通异常后的清理、独立观察失败、启动/权限preflight拒绝、双管6MB和无效UTF8预算，以及Windows拒绝确认的协议fixture。
- Root真实workflow/dispatcher的3项先RED再GREEN：进程真实结束前账号仍锁定；cleanup无法确认持锁并拒绝旧SMS输入/重复claim；启动失败notStarted。回执推进新增1项验证平台观察不能擦除Host进程事实，幂等digest仍只依赖平台证据。
- 独立审查16项通过；补充实际本地进程+模拟强机器回执取消探针保留confirmed/terminationConfirmed，清理结束后释放锁。该探针是模拟平台证据，不代表真实平台发布成功。
- 浏览器7865使用候选backend/dist、真实cookie身份与隔离模拟会话/批准数据，显示“正在停止，等待进程结束”和“停止未确认，账号仍锁定”。截图 `process-states.jpg`；不执行任何真实发布器。

## 仍需继续

主会话、媒体后台任务的完整停止矩阵与可管理任务目录，崩溃后的lease恢复/人工对账，逃组进程的强OS边界，其他操作系统实机验收及真实平台发布仍开放。本功能不增加公开发布尝试，不把进程清理完成当平台未发布，也不把保留的模拟回执当真实平台成功。7860主服务未更新。
