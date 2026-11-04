# BeefTV 本机接入文件

这里保存 Easel 自己的接入配置和协议适配源码，不复制完整 BeefTV 仓库或模型权重。

- `vite.workshop.config.mts`：在官方 Vite 配置上添加本机窄代理。只有 `GET /api/ops` 和 `POST /api/ops/canvas.document.commit` 使用已登记的普通工作区客户端身份，其他路由不自动添加身份；不读取 owner 凭据。
- `ppu-h3-json/`：本机 PPU MiniMax H3 T2VA/I2VA 的 JSON 视频协议源码。
- `ppu-h3-ref11-json/`：独立 Ref11 全参考协议源码；不能与 T2VA/I2VA 的参考字段混用。
- `media_import.py`：通过官方资源、素材与操作接口导入明确选择的本机媒体，返回画布节点、素材、资源及版本；不提交生成。
- `configure_h3.py`：独立安装／核对协议并保存本机模型通道，显式选择 `ref11` 或 `t2va`；`--check` 只读，`--refresh-key` 才显式替换已保存通道 Key，不生成视频。

完整上游源码、二进制和依赖缓存共用 Git 忽略的 `.scratch/beeftv/`。每个 IP 的数据库、素材、配置、客户端和日志独立保存在 `<workspace>/.easel/beeftv/`；脚本及集成工具用 `EASEL_ROOT` 选择当前 IP。旧共享数据只作迁移档案，不作为活动 UI。安装固定为 v2.1.1、提交 `60d22756918127106190d48587b9735c8156dbcf`，由 `scripts/beeftv_runtime.sh install` 校验。上游 `LICENSE`、`NOTICE` 等声明随原仓库保留。

本接入用于用户授权的本机内部制作与验收。上游的新原创部分采用带商业条件的源码可用许可；当前接通不代表取得对外托管、商业分发或多租户产品的授权。部署前按锁定版本的上游许可与第三方声明核对实际范围。

安装、启动、凭据、MCP、原生接口及已知限制见 [运行时文档](../../docs/beeftv-video-workshop.md)。项目创建、交付和审阅操作见 [视频工坊使用说明](../../docs/video-workshop-usage.md)。
