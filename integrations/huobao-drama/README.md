# 火宝原版制作工作台接入

项目内制作页直接运行 `source/frontend/app/views/drama/episode.vue` 和原版 Nuxt/Vue 组件、样式；制作后端直接运行 `source/backend/src` 的 Hono、Mastra、资产、分镜、任务、失败恢复与 FFmpeg 拼接实现。没有用 React 重写一份相似制作页。

上游为 chatfire-AI/huobao-drama，固定提交及原始文件 SHA256 见 `UPSTREAM.json`。原版 `LICENSE`、README 和源码随附。上游采用 CC BY-NC-SA 4.0；复制源码并不额外授予商业授权。

必要适配：

- 前端 API 与媒体地址带响芽项目/IP作用域；返回项目、AI 对话及审核发布通过同源消息交回宿主。
- 引擎仅监听 loopback，全部接口需要私有运行时凭据；凭据不进入网页或 URL。数据库、媒体和运行状态按 IP 放在 Host 私有目录。
- 保留原版四个 Agent 和完整制作逻辑。有独立文本 API 配置时由原版后端执行；选择“当前 AI 底座”时，通过现有响芽对话/Skill Host 执行，结果同步回同一原版工程。角色、场景、道具的并行提取请求会排队，避免互相覆盖。
- 现有 MiniMax H3/Pika 的本机 JSON 协议、鉴权下载和能力档位作薄适配，不改变原版任务与历史版本逻辑。
- 原有响芽视频项目关联火宝一集。剧本、素材、分镜、镜头候选和成片双向同步，冲突保留双方，不静默覆盖。导出成片仍进入原有不可变交付、人工审核及自动发布链路。
- 模型凭据在浏览器响应中隐藏，原版的缩略图/海报函数用于导入已有素材。
- 使用本机可用的 FFmpeg/FFprobe 路径交给原版导出服务；已有生成或拼接任务运行时，延后更新该运行环境。

安装或还原：

```bash
bash integrations/huobao-drama/install.sh
```

前端构建输出 `.output/public`；依赖、构建和私有数据均不入库。安装使用临时 npm 配置，不覆盖用户全局 registry 或登录信息。各 IP 的原版后端在进入项目时按需启动，关闭页面不停止已提交的原版生成任务。

安装脚本还会验证 `better-sqlite3` 并记录该次安装所用的 Node 到本机 `.runtime.json`。启动后端时固定使用这一可执行文件，避免桌面启动器的 PATH 选择另一版本、导致原生 SQLite 模块 ABI 不兼容。运行时不存在或依赖不兼容时会返回明确的 503；使用期望的 Node 重新运行安装脚本即可恢复。已有依赖且当前 Node 已能正常加载它时，也可运行 `node integrations/huobao-drama/configure-runtime.cjs` 重新记录，经验证成功后才会更新本机记录。
