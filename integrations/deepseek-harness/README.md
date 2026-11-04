# DeepSeek Harness migration source

Easel uses selected code and contracts from the official [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) under its MIT license.

- Fixed commit: `ce7073d51254c26cd63b22a60835b8f948deb447`.
- Package version: `0.2.2-alpha.2`.
- Verified reference clone: `.scratch/deepseek-harness-reference`.
- Managed integration clone: `integrations/deepseek-harness/upstream`; HEAD and official origin were verified on 2026-11-03. The full clone is gitignored; migrated production modules are tracked separately.
- Production migration target: `web/frontend/src/vendor/deepseek-harness/{composer,message,navigation}`. A target directory is not proof of a completed capability; the ledger records actual files and verification.
- Copyright and permission notice: [LICENSE](LICENSE).
- Source inventory and actual migration ledger: [upstream-manifest.json](upstream-manifest.json). The `selected_packages` list is a source inventory, not an enabled-feature list. `migrated_files` is only populated after code is actually reused and verified.

See [the migration notes](../../docs/deepseek-harness-migration.md) and [the complete product backlog](../../docs/agent-workbench-backlog.md).

Do not expose this source checkout as a user workspace or copy user credentials into it. Easel's existing IP/account/member permissions and business services remain the integration boundary.

Final tranche verification (2026-11-03): 22 upstream source migrations (10 original bodies verbatim, 12 adapted), with 14 Easel integration adapters recorded separately. Frozen regression/build and a bounded Codex/queue/refresh/navigation runtime path passed; see the manifest verification scope and backlog for incomplete requirements. This is not completion of all 144 comparison items.

Final native-input acceptance: a new Codex thread called the registered `easel_ask_user` tool, showed the style card, received no manual answer, adopted the professional default after 25.0 seconds, and continued a real shell command successfully. Older unregistered native threads must start a new conversation because `thread/resume` cannot append dynamic tools; their IDs and history remain intact. Final whole-suite counts are 2032 backend passed/17 skipped and 356 frontend passed; build/lint/diff-check passed.

## Per-feature phase after baseline 1a4c8b7

D01 is in progress, not accepted and not committed. Current source/hash entries are recorded under `feature_work.D01`; the earlier whole-suite/runtime evidence belongs to the accepted baseline and does not close D01. Three declared cards, text bytes and PNG preview have stage evidence, but HTML relative resources still return403 from the opaque iframe and are being fixed. D08 immutable history is the next independent feature. New Codex threads currently register present; older unregistered threads need a new conversation, and ACP has no corresponding MCP/present registration.


## D01 验证更新（2026-11-03）

D01已在独立功能代码树通过2122项后端、378项前端与真实Codex/下载/PNG/HTML验收；HTML原403已由上游有限资源打包及受控related读取关闭。功能待单独提交，完整记录见 `docs/agent-feature-acceptance/D01.md`。旧阶段的D01 WIP/403描述为历史证据，不代表当前未修复；D08仍未实现。
