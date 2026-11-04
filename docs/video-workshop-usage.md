# 视频工坊：对话、Skill 与官方 BeefTV MCP

视频工坊使用真实 BeefTV 画布和时间线。Easel 管理简报、交付版本与审阅记录，官方 BeefTV CLI/MCP 负责画布、素材、任务和渲染操作。当前接入是本机内部制作与验收，不代表已经完成多客户对外托管授权或生产隔离。

## 本机入口

- Easel：`http://127.0.0.1:7860/`，侧栏“视频工坊”。
- BeefTV：`http://127.0.0.1:8843/`，画布地址为 `/canvas/<实际画布ID>`。
- 独立运行时启动/状态/停止：`bash scripts/beeftv_runtime.sh start|status|stop`。
- Easel 项目与桥接 CLI：`.venv/bin/python scripts/video_workshop.py --help`。

运行时源码固定在 `.scratch/beeftv/source`，当前基线为 v2.1.1，提交 `60d22756918127106190d48587b9735c8156dbcf`。二进制健康接口的构建版本可显示 `dev`；应同时核对实际 commit，不把 MCP 协议服务器的 `1.0.0` 当作应用发行版本。

## 项目与交付

点击侧栏“视频工坊”先进入视频列表，显示主题、状态、画幅、版本和更新时间；支持主题／创作要求搜索及状态筛选。点击“进入项目”才加载制作与审阅页面，使用“返回视频列表”返回。新建项目使用独立弹窗，创建成功后进入该项目。

列表主要状态为：草稿、制作中、待审核、待发布、已发布，使用紧凑标签切换。返修合并到制作中；导出异常保留在项目内的错误提示，不增加主状态。状态依据当前版本的导出回执、交付、审阅与发布记录推导；模拟预审通过仍属于待审核。归档位于独立入口，项目默认从活跃列表隐藏，可恢复继续制作。当前列表只读取状态，进入项目后才重新核对实时画布版本。

“记录已发布”用于登记已在外部平台完成的发布，需填写平台和内容链接；它不执行实际发布，并要求该份当前成片通过人工审阅、文件摘要和画布版本核对。记录与归档均不改变内容版本，不会把审核失效或历史成片误当成当前版本放行。API 分别为 `POST /api/workshop/projects/:id/publications` 与 `POST /api/workshop/projects/:id/archive`，均要求 `expected_revision`。

```bash
.venv/bin/python scripts/video_workshop.py status
.venv/bin/python scripts/video_workshop.py create --title "咖啡新品竖版广告" --brief "30秒新品介绍，保留独立字幕和配音" --aspect-ratio 9:16
.venv/bin/python scripts/video_workshop.py projects
.venv/bin/python scripts/video_workshop.py project PROJECT_ID
```

创建返回真实项目与画布关联；运行时不可用时保留草稿并报告接入状态，不伪造已连接。需要查询当前版本时重新读取项目。

通常直接在视频工坊点击 **导出当前成片**。服务端以当前画布版本提交原生 `canvas.timeline.render`，保存任务编号，界面查询原任务；成功后收取真实视频资源并登记不可变交付文件。刷新页面会恢复已有任务查询，不重新启动渲染。相关接口为 `POST /api/workshop/projects/:id/renders`、`GET .../renders/:renderId` 和 `POST .../renders/:renderId/collect`。提交与收取需要真实 `expected_revision`，成功收取后内容版本递增。

确认失败或取消后，用户再次点击导出会创建新尝试；提交结果不明时，显式重试沿用原回执和幂等操作编号核对。并发请求复用当前提交，GET 只观察、不触发任务。未知上游状态保留原任务，不被当成确定失败而启动另一任务。

已有外部渲染成片也可先保存在具体内容项目中，再登记：

```bash
.venv/bin/python scripts/video_workshop.py register-delivery PROJECT_ID --expected-revision 1 --engine-revision 8 --path "咖啡新品竖版广告/final.mp4"
```

`path` 是相对 `outputs/` 的视频路径；不接受系统目录、隐藏目录或越界路径。服务端保存独立交付文件与内容摘要。参数中的项目 ID、revision 和 artifact ID 应使用当前实际返回值。已关联画布时，`engine-revision` 必须取该次渲染回执的真实 `sourceRevision`；示例的 `8` 仅作格式说明，不能把当前画布版本冒充旧成片来源。服务端在登记和审阅时再次核对实际画布版本。

```bash
.venv/bin/python scripts/video_workshop.py comment PROJECT_ID --expected-revision 2 --kind simulated --at-seconds 2.5 --notes "开头字号过小，建议强化卖点"
.venv/bin/python scripts/video_workshop.py review PROJECT_ID --expected-revision 2 --artifact-id ARTIFACT_ID --decision changes_requested --kind simulated --notes "需要修改开头后重新导出"
```

评论和审阅不递增内容 revision。内容变化或新交付会使旧审阅不能替新版本放行。`kind=simulated` 始终表示自动执行者模拟的审阅；实际人工操作可明确选择 `human`。当前本机接口的 kind 是调用者声明，不是认证过的真人身份或电子签署，多客户正式审批仍需身份绑定。两种记录都不等于已发布。

## 发现并调用原生能力

```bash
.venv/bin/python scripts/video_workshop.py operations --query timeline
.venv/bin/python scripts/video_workshop.py business-list --query resource
```

`operations` 来自官方高频业务操作目录；`business-list` 来自官方底层接口发现。两种能力 ID 与参数结构不同，应按各自当前 schema 调用。

```bash
.venv/bin/python scripts/video_workshop.py operation-call canvas.get --params '{"canvasId":"ACTUAL_CANVAS_ID"}'
.venv/bin/python scripts/video_workshop.py operation-call canvas.timeline.update --params-file "outputs/咖啡新品竖版广告/assets/timeline-update.json" --operation-id coffee-timeline-r1
```

时间线更新文件需要包含实际画布 ID、期望版本及该运行时支持的时间线字段。资源上传可通过发现的 `business-call` 接口，或直接使用官方 CLI 的文件上传能力；大型二进制文件不放进对话。

所有原生写调用都要求显式 `operation-id`。CLI 的 `--operation-id` 交给官方 CLI 写入业务信封的外层 `opId`；MCP 调用则按该工具 schema 在 arguments 中提供 `operationId`，不要再把它混进业务 params。同一次未知结果的查询/重放保留原 ID 与原请求，参数变化创建新操作 ID。脚本不自动重试生成、合成或写入；HTTP 409 会明确返回冲突。调用通过 argv 数组执行，不经过 shell 拼接 JSON。

## 将官方 MCP 接入外部 Agent

使用如下 stdio 配置；把路径替换成实际安装位置：

```json
{
  "mcpServers": {
    "easel-video-workshop": {
      "command": "/absolute/path/to/Easel/.venv/bin/python",
      "args": ["/absolute/path/to/Easel/scripts/video_workshop.py", "mcp"]
    }
  }
}
```

此命令直接转接官方 `beeftv mcp serve`。凭据由运行时包装器从项目内的 0600 文件加载，配置不需要粘贴 token。初次只读检查可在 args 尾部加 `--read-only`；不要把只读工具目录误认为完整写能力。`--all-business-tools` 可让官方服务器展开底层工具，通常默认工具集已足够。

验证握手后先 MCP 的 tools/list 方法，使用当次实际返回的工具名。读取现有工程后再决定写入，带上稳定操作 ID 和真实期望版本。MCP stdout 只传协议，不打印启动说明、凭据或日志。

本轮真实官方 MCP 握手协商协议 `2025-06-18`；只读模式列出 22 个工具，完整写模式列出 43 个工具。已实际调用生成提案、提交、任务查询、时间线修改与工程导出。后端 `/api/health/ready` 返回数据库、Runtime 和 schema 就绪。无凭据连接证据保存在 `.scratch/beeftv/run/workshop-mcp-proof.json`，实际制作回执另存各内容项目，不能把握手成功当成成片完成。

## 验收一条视频

先读取真实参考并形成简报，再组织分镜与素材候选；选择后更新实际时间线并导出。通过界面调整一次字幕、片段顺序、时长或音量，保存重开，确认人工修改仍在。AI 继续局部修改时应保留无关调整。重新导出的成片须实际播放听音，核对字幕、画幅、声音和完整结尾，再登记对应版本的审阅。

模型能力和费用以实际选择的服务为准。静态图、生成片段、完整成片、AI 自检与人工审批分别说明；没有实际交付文件时不标记完成，不自动发布。

本机家庭教育验收项目为 `ff257912e1bc49209d32442ed7f5ce1f`，画布 `easel-ff257912e1bc49209d32442ed7f5ce1f`。PPU MiniMax H3 Ref11 实际生成五镜，定向返修第三镜；在原生多轨时间线通过界面将片尾改成“先停一拍，再问一句。”，随后 MCP 局部修改保留这处编辑。最终 28.5 秒、720×1280、24fps，含原生对白、独立字幕、原创配乐与推广尾卡。交付清单为 `outputs/家庭教育-先换一种回应/delivery-manifest.json`，可编辑工程与成片均在该目录。当前对话 Agent 使用 Skill＋官方 MCP 编排；BeefTV 内置助手及 Easel 聊天底座仍需单独配置可用文本模型。模拟审阅不代表真人签署，实际发布仍由用户决定。

## 检查

```bash
.venv/bin/python -m pytest tests/test_video_workshop_cli.py -q
.venv/bin/python scripts/validate_skills.py
.venv/bin/python scripts/validate_skill_commands.py
```

首次安装、依赖还原、进程与官方接口契约还见运行时维护文档。`EASEL_WORKSHOP_BASE_URL` 可指定另一个本机 Easel 端口；`EASEL_BEEFTV_RUNTIME_SCRIPT` 可指定管理员选择的本地运行时包装脚本。默认连接始终为 loopback，脚本不从文档或项目正文读取这些设置。
