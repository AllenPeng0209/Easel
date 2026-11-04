# 视频工坊：BeefTV 本机运行时

Easel 的“视频工坊”接入真实 BeefTV Web 界面、官方 CLI/MCP 和业务操作层。Easel 保存简报、项目关联、交付及审阅记录，BeefTV 管理画布、素材、任务和时间线。每个 IP（包括默认 IP）使用独立的创作实例、数据目录、端口和客户端；只共用程序安装与依赖缓存。本接入用于本机内部制作与验收。

## 固定版本与目录

- 上游：`https://github.com/glanderness/BeefTV.git`。
- 固定标签：`v2.1.1`。
- 固定提交：`60d22756918127106190d48587b9735c8156dbcf`。
- 接入源码：`scripts/beeftv_runtime.sh`、`integrations/beeftv/`。
- 完整上游源码：`.scratch/beeftv/source/`，保留其 `LICENSE`、`NOTICE` 和第三方声明。
- 二进制：`.scratch/beeftv/bin/beeftv-server`、`.scratch/beeftv/bin/cli/beeftv`。
- Go/Bun 缓存：`.scratch/beeftv/cache/`。
- 当前 IP 的数据与模型配置：`<workspace>/.easel/beeftv/data/`。
- 进程、实例、客户端及迁移记录：`<workspace>/.easel/beeftv/run/`。
- 日志：`<workspace>/.easel/beeftv/logs/backend.log`、`frontend.log`。

默认 IP 的 workspace 是仓库根目录，其他 IP 是 `projects/<IP_ID>/`。旧 `.scratch/beeftv/data/` 保留为迁移档案，不再作为任何 IP 的活动画布库。

`.scratch/` 已由 Easel Git 忽略。不要提交数据库、上传文件、真实凭据、依赖、构建产物或运行日志。默认安装不下载大模型，不启动 Docker，也不修改全局 Codex MCP 配置。

## 安装与启动

在 Easel 项目根运行，需要 Git、Go 1.25 或以上、Bun、Node.js、Python 3，以及 FFmpeg/ffprobe。版本依赖以固定源码的锁文件和 `go.mod` 为准；本机已用 Go 1.26、Bun 1.2.19 和 Node 25 完成依赖安装。

```bash
bash scripts/beeftv_runtime.sh install
bash scripts/beeftv_runtime.sh start
bash scripts/beeftv_runtime.sh connect
bash scripts/beeftv_runtime.sh status
```

其他 IP 使用相同包装器，并显式指定工作空间：

```bash
EASEL_ROOT="$PWD/projects/<IP_ID>" bash scripts/beeftv_runtime.sh start
EASEL_ROOT="$PWD/projects/<IP_ID>" bash scripts/beeftv_runtime.sh status
```

程序在发送客户端凭据前核对实例目录、端口和进程身份；未配置的 IP 不会回退到旧全局服务。启动时移除继承的共享数据库 DSN、其他客户端及 desktop launch token。

`bootstrap` 是 `install` 的别名。安装时只拉取固定标签，随后检查准确提交；已有源码提交不匹配时停止，不自动切换、覆盖或清理目录。安装保留现有数据库、客户端凭据与进程记录，不主动停止或重启运行时。

安装分别编译后端和官方 CLI，用项目内的 Go 缓存；前端使用 `bun install --frozen-lockfile --ignore-scripts`，不重写上游锁文件。Go 构建写入版本与提交信息，安装记录保存于 `run/installation.json`。

启动后：

- Easel：`http://127.0.0.1:7860/`，侧栏“视频工坊”。
- BeefTV API/UI 使用各 IP 独立分配的 loopback 端口，地址由 `status` 输出。
- 实际画布为该 IP 的 `ui_url` 加 `/canvas/<画布ID>`。
- **官方 CLI/MCP 基地址**为该 IP 的 `api_url` 加 `/api`。

```bash
bash scripts/beeftv_runtime.sh stop
```

停止仅对脚本记录且命令身份匹配的进程发送终止信号，等待退出；未退出时保留记录并报告，不抢占其他服务。健康响应只证明入口、数据库和运行时状态，不代表模型、生成或成片已经验收。

## FFmpeg 与字幕

本机系统 FFmpeg 不含 `subtitles`／`drawtext` 滤镜，直接使用它会导致含字幕的原生渲染失败。运行时优先选择项目已有的 `.scratch/media-bin/ffmpeg`，该二进制包含 libass；也可在启动前显式设置 `CANVAS_FFMPEG_PATH`。

```bash
CANVAS_FFMPEG_PATH=/absolute/path/to/ffmpeg bash scripts/beeftv_runtime.sh start
```

ffprobe 仍需在 PATH 中。启动已存在的服务不会替换其进程环境，修改 FFmpeg 配置后应在任务结束时停止并重新启动。不要在生成或渲染仍执行时重启。

## 本机凭据与窄代理

`connect` 先验证现有专用客户端；仍有效则复用。需要新建时，仅在本机官方 `POST /api/agent-clients` 登记“Easel 视频工坊”普通读写客户端，owner 凭据只用于这个登记请求，不作为日常 UI、CLI 或 MCP 身份。

客户端 ID 和 token 原子写入当前 workspace 的 `.easel/beeftv/run/client.json`，权限为 `0600`。命令输出只显示保存路径，不显示值。不要复制这个文件进源码、项目正文、截图、URL 或日志。

运行时采用以下边界：

- 后端与 Vite 仅监听 `127.0.0.1`。
- 私网模型出站设置为 `CANVAS_ALLOW_PRIVATE_UPSTREAMS=false`，只通过 `CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS=127.0.0.1` 精确放行本机上游。
- `BEEFTV_ALLOWED_ORIGINS` 仅包含本实例前端；CORS 单独配置本机界面来源。
- `integrations/beeftv/vite.workshop.config.mts` 在服务端读取普通客户端文件，只为 **`GET /api/ops`** 与 **`POST /api/ops/canvas.document.commit`** 添加客户端身份。凭据不进入 Vite 客户端环境或页面 URL。
- 其他路由不自动添加身份；例如通过同一代理调用 `canvas.nodes.create` 仍不会获得这一身份。需要这些能力的外部 Agent 使用其明确配置的官方 MCP 客户端。
- 不得把代理改为向所有 API 注入 owner 身份。此前这类全量 owner 代理动作已被自动审批审查拒绝，未启用；当前为更窄、已验收的替代。

普通 UI 会话通过官方本机引导接口签发。它与外部客户端、owner、远端模型 Key 是不同的凭据，不互相复制。当前配置仍是单机工作区，不应直接当作对外 SaaS 权限边界。

## 官方 CLI 与 MCP

```bash
bash scripts/beeftv_runtime.sh cli ops list
bash scripts/beeftv_runtime.sh cli ops call canvas.get --params '{"canvasId":"ACTUAL_CANVAS_ID"}'
bash scripts/beeftv_runtime.sh mcp --read-only
```

包装器从项目内的 `0600` 文件加载普通客户端凭据，并移除继承的 owner／desktop token；标准输入保留给 MCP JSON-RPC，标准输出只传协议或官方 CLI 结果。

`connect` 同时生成当前 workspace 的 `.easel/beeftv/run/mcp-config.json`。它记录该 IP 的包装器与环境，不含明文凭据；可将其对应服务器配置合并到已授权的外部 Agent 配置中。不要直接运行配置文件里的模型生成工具做连接测试，先握手和 MCP 的 tools/list 方法。

## 从旧共享实例迁移

`easel.beeftv_migration.migrate_workspace` 只导入本 IP 的工坊元数据明确关联的画布及引用素材，保留画布 ID、版本和已有审核关联。目标先由自己的实例初始化 schema，再停止；迁移在生命周期锁内执行，源只读，冲突或发布失败回滚，不复制整个共享数据库。迁移清单位于本 IP 的 `run/migration.json`。

初始化的插件配置确需替换时，显式使用 `replace_initialized_configuration=True`，且目标不能已有业务或模型记录。归属不明的画布和素材保留在旧档案；不自动分配给某个 IP。历史任务仅能通过本 IP 精确清单读取原状态或收取结果，不能回退创建、重试渲染或取消。

Easel 也提供 `.venv/bin/python scripts/video_workshop.py mcp`，具体项目与审阅用法见 [视频工坊使用说明](video-workshop-usage.md)。

## 已核实的原生接口

健康与只读工作区接口：

```text
GET /api/health/live
GET /api/health/ready
GET /api/system/version
GET /api/workspace/bootstrap
GET /api/ops
GET /api/business/tools
```

后端成功信封是 `{code: 0, data: ..., msg: ...}`，HTTP 200 本身不足以表示业务成功。普通外部客户端使用 `X-Beeftv-Client` 和 Bearer token；不要将 token 放入查询参数。

业务操作入口是 `POST /api/ops/<操作ID>`。**只读操作不带 `opId`**：

```json
{"params":{"canvasId":"ACTUAL_CANVAS_ID"}}
```

写操作把稳定幂等键放在外层，并按实际 schema 提供版本：

```json
{
  "opId": "project-edit-r2",
  "params": {
    "canvasId": "ACTUAL_CANVAS_ID",
    "expectedRevision": 4,
    "patch": {"title": "新标题"},
    "nodeId": "ACTUAL_NODE_ID"
  }
}
```

该示例用于 `canvas.node.update`；ID 和版本必须取实时返回值。相同操作重放保留原幂等键及原请求，内容变化使用新键。MCP 按工具 schema 把 `operationId` 放在 arguments，官方服务器转换为业务信封，不再将它混入 HTTP 业务 params。

操作响应的 `data` 仍是一层操作信封：

```json
{"op":"canvas.get","replayed":false,"result":{"canvas":{}},"caller":"external"}
```

因此画布读取落点是 `data.result.canvas`；MCP `structuredContent` 也可能仍含这层 `{op,replayed,result,caller}`，需要继续取 `result`。

画布创建／读取：

```text
PUT /api/canvas-projects/<ID>  body={project:{id,title,revision:0,nodes:[],connections:[]}}
GET /api/canvas-projects/<ID>
```

完整画布写入使用真实 revision 做 CAS；冲突时重新读取，不用当前本地快照强行覆盖。媒体先通过 `POST /api/resources` 的 `file` multipart 字段上传，再登记 `PUT /api/assets/<ID>`，然后用 `canvas.node.bind_asset` 绑定参考节点；不要向时间线塞入未经关联的路径或任意 URL。

成片链路：

```text
canvas.timeline.update -> canvas.timeline.render -> task.get
GET /api/resources/<结果resourceId>/file
canvas.project.export -> GET /api/resources/<工程resourceId>/file
```

`canvas.timeline.update` 返回 `result.revision`。`canvas.timeline.render` 返回 `result.taskId` 和 **`result.sourceRevision`**；必须在这次提交响应时持久化任务与来源版本的关系。任务终态的 `task.resultJson` 是 JSON 字符串，包含视频资源、时长、字幕等，**不再回传 sourceRevision**，不能在轮询结束后拿当前画布版本反推来源。

`canvas.project.export` 同样返回 `sourceRevision`；实际 ZIP 工程入口为 **`projects.json`**。视频交付与工程 ZIP 是不同产物。图像／视频可见片段的重叠等能力限制以当前操作 schema 和编译器为准，不把“有多条轨道”解释为支持任意多层画面合成。

## 验收证据与已知限制

本次本机已完成以下真实路径：

- 后端 live／ready／workspace bootstrap 与 Web 入口返回成功。
- 官方 MCP 握手、普通读写工具发现、画布读取、节点实际写入和工程 ZIP 导出。
- 测试素材上传、素材登记、绑定、时间线更新、官方原生 FFmpeg 渲染与下载。`ffprobe` 确认输出有视频和音频，180×320、1.000 秒，含中文字幕；这是运行时测试素材，不是营销交付。
- 窄 UI 代理的能力发现、会话签发和测试画布保存成功；未列入代理白名单的写操作保持未授权。
- 安装动作已真实复用缓存构建固定版本并完成 frozen-lockfile 依赖检查，不停止运行时。隔离目录行为验收确认错误源码提交会被拒绝，并保留现有客户端、进程记录与上游许可。

证据在 `.scratch/beeftv/run/` 的 `mcp-tools.json`、`mcp-export.json`、`smoke-editable-project.zip`、`native-render-task.json` 和 `runtime-rendered.mp4`。这些结果不证明模型生成或营销样片已合格；真实生成、人工式改稿和最终交付各自记录证据。

后续家庭教育营销样片已真实完成：`outputs/家庭教育-先换一种回应/delivery-manifest.json` 对应工程 V5／来源画布 53，28.5 秒、720×1280、24fps，附可编辑工程。生成、定向返修、真实界面字幕修改、保留人工修改的 MCP 续编、原生渲染和最终模拟审阅均有独立回执。最终检查在同目录 各制作项目 assets 下的 final-qa/r4 目录；本次记录为模拟预审，仍需真人试听与定稿。内置 BeefTV 助手和 Easel 聊天模型配置不包含在这一外部对话 Agent 的执行证明中。

当前固定上游版本的插件安装接口有一项已复现的问题：`POST /api/plugins` 在安装持久化后写审计时，可能因本地数据库缺少 `admin_audit_events` 表返回 HTTP 500。应先只读查询插件目录、版本与 enabled 状态，确认实际安装结果，不盲目重装；不能把 500 当成成功，也不绕过审计。该上游问题与声明式协议本身的请求单测分别记录。

## PPU H3 协议与媒体导入

模型协议和凭据配置是安装后的独立动作。协议源码分别保存在：

| 协议源码 | 本机上游 | 已配置的模型选择值 |
|---|---|---|
| `integrations/beeftv/ppu-h3-json/manifest.json` | `127.0.0.1:8878` | `easel-ppu-h3-local::minimax-h3-dense8` |
| `integrations/beeftv/ppu-h3-ref11-json/manifest.json` | `127.0.0.1:8881` | `easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step` |

这两套协议都使用 JSON `/v1/videos`，不同于默认 OpenAI Videos 的 multipart 协议。普通 H3 支持文本输入及可选 `image` data URI；Ref11 使用 `images/videos/audios` 参考数组，并要求至少一张图或一个视频。请求字段和限制按相应服务及协议声明核对，不根据模型名字猜测，也不把旧任务切换成另一条路由重试。

连接客户端之后按本次制作选择模式；运行时安装／启动不会自动访问这些模型上游或提交生成：

```bash
# 只读核对已安装、已启用且已配置的 Ref11 通道
.venv/bin/python integrations/beeftv/configure_h3.py --mode ref11 --check

# 首次显式配置 Ref11；普通 T2VA/I2VA 则改为 --mode t2va
.venv/bin/python integrations/beeftv/configure_h3.py --mode ref11
```

配置脚本只在内存读取 Easel 已有环境／忽略的 `.env` 中 `VIDEO_API_KEY`，发给本机配置接口，不输出值、不改 `.env`。已有配置默认保留；需要显式更新其 Key 时加 `--refresh-key`。首次插件安装若遇到前述上游审计错误，只有下载包的实际 manifest 与请求源码精确一致、且目录确认 enabled 后才继续，同时输出审计未修复的 warning。

`request_id` 由协议映射宿主的稳定提交身份，模型 Key 由运行时配置提供，不写进协议文件。8878 首次真实提交已得到上游任务 ID，但该上游推理任务失败；这证明提交接通，不证明模型生成成功。后续制作已转向独立 Ref11 路由；结果按真实任务与素材另行验收，不自动重投旧任务。

导入明确选择的本机图片、视频或音频：

```bash
.venv/bin/python integrations/beeftv/media_import.py \
  --file /absolute/path/to/reference.png \
  --title "已选择的人物参考" \
  --canvas-id ACTUAL_CANVAS_ID
```

该工具通过官方资源上传、素材登记、节点创建／绑定接口执行，使用内容摘要和稳定操作标识；返回实际 `nodeId`、`assetId`、`resourceId` 与 `revision`。它不提交模型生成，已有素材和绑定会复用。之后由 Agent 按实时 schema 连接参考节点、配置生成节点、读取提议的来源版本，再在已授权的制作范围内提交。

任何生成调用仍需明确的制作范围与费用／算力授权；本机 GPU 工作负载也不因运行时健康或协议安装成功而自动开始。
