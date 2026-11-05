# C19 · 会话目录并发初始化修复

日期：2026-11-03。状态：已验证；独立提交标题 `fix(chat): avoid journal-mode races during catalog startup (C19)`。

## 问题与最小修复

每个连接执行journal_mode=WAL，导致首次建库的并发reader/claim竞争升级锁。仅移除重复模式协商，保留10秒连接timeout、BEGIN IMMEDIATE、主键与独占事件文件；既有WAL库不降级。不以thread lock或sleep掩盖跨连接竞争。

这是响芽 SQLite宿主适配修复，沿用DeepSeek session-controller的有序写入、request identity与单writer语义，不冒称复制一份不存在的上游Python实现。

## 验证

- 新4项真实SQLite消费者回归：旧实现1失败/3通过；新实现4通过。覆盖DELETE库活跃reader、既有WAL保持、2/8并发只有一winner、重放前缀不被截断。
- Root独立压力：修复前200次claim中20次SQLITE_BUSY；修复后1000轮×8=8000次零错误，每轮单winner，事件前缀保留，integrity_check全部ok。
- Root相关6组：54 passed、1旧弃用warning，exit0。命令覆盖concurrency/history/catalog-access/event-recovery/multiuser/snapshot-ownership。
- 实施者另一组相关回归77通过，编译与diff-check通过。
- 日志：`.scratch/c19/root-red.json`、`root-green.json`、`root-regression.log`；完整隔离复现脚本和上游语义定位在`.scratch/c19/findings.md`。

## 范围

本提交只修目录初始化竞争，C23分页等不在此关闭。没有读取或更改生产用户数据库；没有调用真实模型。未改变D01当前文件语义，D08仍待开发。
