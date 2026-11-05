# PPU Ref2VA 执行与成片验收

平台内真实生成只用当前会话的 `easel_video_generation`，工程登记／交付走 `easel_workshop_delivery`。下方供应商 HTTP 内容是来源技能记录的 2026-10-28 部署快照，供 Host 适配维护者理解，不是 Native AI 的可执行接口或密钥读取授权。能力与部署可能变化；以当前 Host `catalog`、实际请求回执和已维护的适配契约为准。此参考不授权新部署、重启其他任务、更改凭据或新增资源。

## 站内执行入口

1. 读当前 IP 与会话的真实 `easel_video_generation` schema 和 `catalog`，核对模型选择、输入模态、时长、画幅及能力状态。仅有配置、健康接口或监听不等于推理就绪；能力缺口交平台操作者处理，不让 Native AI 查密钥、开隧道或直接请求供应商。
2. 将脚本编译成完整英文六段式 `prompt`，保留 `<d>[Chinese] 原词 </d>` 与稳定声音 ID；参考文件在本 IP 内真实存在，并按每段实际模态顺序编号。冻结前核对提示词、原词、声音归属和素材用途。
3. 在真实工坊中登记计划；多片制作通过 `plan → approve_plan` 冻结固定范围与真实决定。已有授权覆盖的现有 PPU 算力不重复询价，Host 范围卡、额度与资源来源仍须留证；未知报价不代表零成本或无限许可。
4. 获批后每片 `prepare(workshopId,clipId,planId)`，再以同一工坊／片段 `propose → submit → status → collect`。`prepare` 从私有计划读取规格，不重传 prompt 或连接信息。未知只查原任务，不换 ID、不重置 claim、不直接 HTTP 重投。
5. `collect` 的 `production_segment` 是生成素材，不是全片；验收后剪辑、字幕，使用同一工坊的真实交付／渲染链生成完整 master，再 `present` 固定实际文件版本。完整动作参数及需要修订计划时的 `supersedesPlanId` 契约见[制作计划](../../family-education-pipeline/references/production-plan.md)。

只有本会话确实注册的动作才可调用；没有相应能力时报告缺口。不能把下面的 `seconds / size / seed / images / request_id` 等供应商字段直接加进 Host tool payload 或制作清单。

## 当前 Pika 适配（2026-11-05）

旧 7777 机已切换至 `easel-ppu-pika-h3-local::pika-h3-flex-6step`，默认 IP 与老白 IP 使用该独立模型身份；旧 Ref11 已停用于新生成，历史任务与素材仍保留。当前能力先以本会话实际 `catalog` 核验，不套用下方旧 Ref11 快照。

当前 Pika 入口为官方 6 步、`480p`（Host 字段小写）、5—15 整数秒、24 fps 与原生音频，支持纯文生或一张本 IP PNG/JPEG 图生；此适配没有视频、音频参考输入。WAV 不能作为音色硬锁，提示词的稳定声音 ID 也不是声音克隆权限或保证。英文六段式场景与 `<d>[Chinese] 原词 </d>` 继续用于表达创作意图，实际台词、口型和连续性仍须验收。

原生 16:9 为 832×480、9:16 为 480×832；Host 留存原片和 SHA，另生成 854×480 或 480×854 的交付片。单张 I2VA 不代表跨镜完全锁脸、同声或精确零帧衔接。模型改变后不能沿用旧冻结计划直接提交；按[制作计划](../../family-education-pipeline/references/production-plan.md)受控替换，保留已消耗记录，在剩余范围内列出新片段并取得真实 Host 新范围批准。

## 供应商部署快照（仅适配参考）

- 来源部署 Base URL 为本机 `8881` 授权转发。Host 维护者可用既有认证核验 `/health`、`/v1/models`；Native AI 从 Host 获取能力，不自行连接或读取认证。来源网关没有 `/openapi.json` 路由，404 不代表服务故障，也不代表队列为空。
- 模型：`minimax-h3-ref2va-ref11-8step`；8 NFE 实验蒸馏，16 PPU。此模型不是原始 49 步完整配置的质量等价承诺。
- 请求秒数 1–14；画幅用 `size` 传 `16:9` 或 `9:16`，原生分别 1344×768、768×1344，24 fps。
- 视频帧数需满足模型的 17 帧对齐：从 `ceil(seconds × 24) + 1` 开始，加 `(5 - frames) % 17`。因此请求 5 秒会得到 124 帧，实际 5.1667 秒；请求 6 秒会得到 158 帧，实际 6.5833 秒。切点仍按请求的创作时间轴规划，成片拼接与声音交界使用下载后实际帧数/时长。
- 至少一张图或一段视频；纯 T2VA 不支持。图片最多 9、视频最多 3、音频最多 3，参考总数最多 12；含音轨的参考合计最多 3。音视频引用的时长须大于 0 且不超过 15 秒。
- 单文件图片上限 10 MiB、视频 50 MiB、音频 15 MiB。使用服务支持的格式和 MIME；上传前用真实媒体探测结果核对。
- 原生音频始终开启；无 `enable_audio`。无独立 `negative_prompt`、`aspect_ratio`、`resolution`，也没有首帧 `image`、尾帧 `last_image` 的绑定参数。不要把其他供应商或源 DOCX 的参数照搬过来。
- `prompt` 去空白后须非空且不超过 12,000 字符；`seed` 为 0–4,294,967,295 的整数。

## 供应商请求映射（非 Host 参数）

认证由 Host 管理，Native AI 不读取令牌文件或导出密钥；凭据不进入提示词、制作清单、命令参数、共享包或日志。以下编号描述适配器与供应商之间的协议，不是站内操作步骤；示例为字段形状，不可直接执行。

1. 来源服务 `POST /v1/files` 的 multipart 字段为 `file / purpose`，purpose 为 `ref_image / ref_video / ref_audio`；也支持带 purpose 查询参数的原始字节上传。当前 Host 可直接绑定已核验媒体的内联引用，不要求 Native AI 另做上传。
2. 来源服务 `POST /v1/videos` 的白名单形状如下，`seconds` 与别名 `duration` 只能取其一；请求由 Host 适配器构造。

```json
{
  "model": "minimax-h3-ref2va-ref11-8step",
  "prompt": "subject_definitions: ...\nsummary: ...\nretention_analysis: ...\ndetailed_description: [Shot 1] ...\noverall_soundscape: ...\nnon_diegetic_music: ...",
  "seconds": 5,
  "size": "16:9",
  "seed": 20261028,
  "request_id": "cinema-example-shot01-v1",
  "images": ["file_IMAGE_ID"],
  "videos": [],
  "audios": []
}
```

参考数组可用上传响应 `id` 的值，即 `file_` 加 32 位十六进制的 ID，或受支持的 `{ "data_url": "data:image/png;base64,..." }` 对象；本地路径或外网 URL 不能直接放进去。不要在日志保存内联 base64。图片、视频和音频分别按各自数组从 1 编号：`Image 1` / `Video 1` / `Audio 1`。没有额外的顶层 mapping 参数。

3. 来源服务 `GET /v1/videos/{id}` 的状态为 queued / in_progress / completed / failed；它们不直接定义 Host 状态枚举。Host 保留原任务与 `submission_state`；submitting / submission_unknown 不能当未提交。Native AI 只通过 `status` 观察原任务，没有 job ID 时也不自行 POST 恢复。
4. 来源服务完成后提供 `GET /v1/videos/{id}/content`。适配收取须保留原片、临时下载与 SHA 校验；该部署的 `output.sha256` 可能在首次下载物化后才出现，维护者应在下载后重读并核对。站内使用 Host `collect`，不能自行读取 Bearer 下载或把排队、受理当成收片完成。

供应商 `request_id` 用于可追踪幂等，同一 ID 不能用于不同内容。修改已冻结范围须先走 Host 支持的受控替换／新批准流程，不能仅改请求 ID。来源 content 的 409 not_ready 或 502 invalid_native_result 不授予重新生成权限；Host 如有原任务安全收取／恢复能力，只用其明确动作。来源接口没有取消端点，客户端超时不代表远端取消。默认串行利于逐镜验收，不能因此重启或抢占其他任务。

## 多段承接

先生成并检查首段，确定本镜进入成片的入点与出点。需要状态承接时，再提取出点最后可见帧；下一段可在获准范围内单用该帧或按需要搭配角色锚点，分别说明状态承接和身份保留，不强制多图并喂。新增未来尾帧不在原冻结参考内时，不能私改原计划；通过 Host 支持的新范围流程落实。严格 18 秒的 3 镜可各选 144 帧：每镜请求 6 秒的原片为 158 帧，若取开头 6 秒，承接帧应为零基帧号 143，不能取不会进入成片的原片末帧 157。若只裁开头而保留原片末端，末帧仍可承接。此计算例不授权截断对白；精确总时长仍须计入转场重叠和音频边界。

来源技能记录的一次三图范例曾生成不期望的全景、近景、手部画面轮播与叠化，未满足稳定情绪近景。在该次授权返修中，定向降级为一张承载目标景别、人物服装和光位的参考，再用编辑切接承接。这是历史观察下的选择，不是“多图必蒙太奇”或“单图必稳定”的规律，不构成本次失败归因或自动改计划的依据。

仅在实际所选适配的 `catalog` 支持音频或视频参考时，才可在任务授权及其时长/数量限制内引用，并记录对应编号；当前 Pika 适配不支持此输入。

上述旧 Ref11 部署只是 Ref2VA 软参考：写“首帧对齐”不会开启该旧入口不存在的严格 I2VA/FL2VA 绑定。不要在交付中声称精确 0 帧衔接、完全锁脸或完全同音色。必要时按真实首尾画面选择匹配剪辑、道具特写或反应镜。

## 验收与清单

根据实际产物运行 ffprobe，检查分辨率、帧率/帧数、时长、视频/音频流、采样率、声道、解码和文件 SHA256。提取每段开头、中部、尾部及切点邻帧，实际看片检查动作、脸、手、服装、道具、轴线、字幕与水印；听原生音频并核对原台词。ASR 可辅助逐字核对，不能代替听音和口型观察。

用户已直接听出原词或说话人不符时，足以记为该项未通过，不反复要求重复证明。同一问题在源片与转码片均存在时，保留两份证据分别记录；8 NFE、WAV 引用或提示格式不是已经证实的根因。修正提示组织方法后仍须验看新结果，不能自动写成质量已解决。

多段片保留每段原始 MP4 和原生音轨，成片使用真实时长处理接缝。若另做淡入淡出、响度调整、环境声桥接、字幕或配乐，保存独立后期版并记录；不要默认为源模型能力。

建议运行清单保留这些字段（按任务精简即可）：

```text
source_document_sha256, brief, storyboard_path, continuity_ledger_path,
model, base_url_without_credentials, request_id, job_id, seed,
requested_seconds, requested_size, prompt_path,
references[{modality, index, file_id, sha256, provenance, retention_intent}],
status, submitted_at, completed_at, generation_seconds,
original_path, original_sha256, response_output_sha256,
actual_frames, fps, actual_duration, dimensions, audio_probe,
qa_observations, repairs, edit_operations, final_path, final_sha256
```

没有实际量测的字段记为未测，不能拿既往基准填入本次。报告每镜成功/失败、已通过的检查和余留问题，并提供可播放成片以及原片、提示词、计划与收据的可访问位置。
