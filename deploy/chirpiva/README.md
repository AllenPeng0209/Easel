# Chirpiva 单机部署与切换

这套配置针对当前响芽的完整 Python/React 应用：一台 Ubuntu 24.04 主机、持久磁盘、一个 Uvicorn 进程，以及同机 Caddy HTTPS。后台运营、视频任务、原生 Agent 和浏览器均有状态，不能按静态网站发布，也不能直接开多副本或多个 workers。

当前提供源码包和部署配置；它们不表示域名已购买、服务器已创建或完整业务已经通过云端验收。

## 固定目录与资源

- 真实应用路径固定为 `/srv/chirpiva/app`，不是随版本改变目标的软链接。`PROJECT_ROOT.resolve()` 的 SHA256 前 24 位决定 Host 私有状态目录。
- 服务账户为 `chirpiva`，home 为 `/srv/chirpiva`。Python 虚拟环境、原生 CLI、浏览器缓存都由该账户使用。
- `XDG_STATE_HOME=/var/lib/chirpiva`，Host 状态在 `/var/lib/chirpiva/easel/host-state/<应用路径哈希>/<IP>/`，位于 Agent 工作区之外。
- 可从 4 vCPU / 8 GiB / 100 GiB SSD 作为初始资源方案评估；这不是负载测试结论。模型推理算力另计，当前 PPU 服务不应被当作普通公网 Web 主机。
- 本次按用户最低成本要求实际选用新加坡轻量 2 vCPU / 2 GiB / 40 GiB，首月 39 元且不自动续费，另配 4 GiB swap。采用低并发运行；不能把这一低成本档当作并发容量承诺。当前仅迁移登录用户、IP 与画像，历史素材、会话及旧任务保留本机。
- Python 3.12；本部署选择 Node 24.x 且至少 24.16；仓库默认安装器另外支持 Node 26.1+，但不在这份部署模板中混用主版本。
- 仅公开 TCP 80/443；SSH 限管理员来源。7860、火宝端口、模型端口和远程桌面端口不向公网开放。

## 1. 生成、审核源码包

在当前工作区执行（输出文件必须尚不存在）：

```bash
.venv/bin/python deploy/chirpiva/bundle.py \
  --output outputs/chirpiva-deployment-20261106/chirpiva-source.tar.gz
```

包保留当前已跟踪文件的实际内容；已删除的跟踪文件不恢复。另只允许明确列出的部署文件、测试及 `assets/branding/xiangya-wordmark.png`。品牌图标 `web/static/xiangya-*` 本来受 Git 跟踪，会正常入包。包内不包含 `.env*`、私有画像、用户素材、outputs、projects、`.easel`、Host 状态、浏览器登录态、密钥文件、依赖与构建目录。普通文件中的 PEM 私钥块也会导致失败。

同名 `.manifest.json` 记录每个文件大小、模式、SHA256、保留的删除及整个归档 SHA256。核对清单后才传输。此脚本是路径过滤和私钥块检查，不替代对源代码中意外硬编码凭据的审查；未跟踪的其他业务代码也不会自动入包。

上传后先核验归档 SHA256 与清单一致，再在一个新建空目录中解包检查；绝不把未经验证的任意第三方 tar 以 root 解到生产目录。源码包与数据备份分开传送。

## 2. 在 Linux 目标机安装依赖

以下是目标机管理员操作，不要在 Mac 上执行。先确认服务器与费用已确定。

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev \
  build-essential pkg-config git ffmpeg curl ca-certificates \
  libgl1 libglib2.0-0 fonts-noto-cjk fonts-noto-color-emoji caddy
sudo useradd --create-home --home-dir /srv/chirpiva --shell /bin/bash chirpiva
sudo install -d -o chirpiva -g chirpiva -m 0750 /srv/chirpiva/app /var/lib/chirpiva
sudo install -d -o root -g chirpiva -m 0750 /etc/chirpiva
```

账户已存在时核对 home 与所有权，不能无检查重复创建。把经验证的源码解到固定真实路径。安装官方 Node 24.16+ 的 24.x 版本到服务 PATH，验证发行签名/校验和，不复制 Mac 的 Node、`.venv` 或 `node_modules`。随后：

```bash
sudo -iu chirpiva bash /srv/chirpiva/app/deploy/chirpiva/install-dependencies.sh
sudo /srv/chirpiva/app/.venv/bin/python -m playwright install-deps chromium
```

脚本重建主 React 前端、火宝 Nuxt 前端和 Node 原生 SQLite 模块，并为服务用户安装 Playwright/Patchright 两套 Chromium；不会启动应用或启用 systemd。首次运行需根据 Python wheel 可用性补齐安装错误，不把依赖部分安装误报为完成。

火宝 `install.sh` 生成 Linux 本机 `.runtime.json`，验证 `better-sqlite3` 并记录正确 Node 路径。它按 IP 私下启动，无需再创建暴露公网的火宝 service。视频 SDK 的可选渲染/识别模型、Office 预览所需软件和具体原生 Agent 仍需按实际启用能力安装并逐项验证。

需要社交平台可视登录时，为同一服务账户配置受保护的显示会话及远程查看方式，再设置 DISPLAY；只有 Chromium 或 Xvfb 不代表真人扫码/风控确认已完成。不要把无认证 VNC/noVNC 暴露到公网。

## 3. 安装反向代理与服务配置

```bash
sudo install -o root -g chirpiva -m 0640 \
  /srv/chirpiva/app/deploy/chirpiva/environment.example /etc/chirpiva/environment
sudo install -o root -g root -m 0644 \
  /srv/chirpiva/app/deploy/chirpiva/chirpiva.service /etc/systemd/system/chirpiva.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/chirpiva.service
```

`EASEL_EXTRA_HOSTS` 和 `EASEL_EXTRA_ORIGINS` 必须在进程导入应用前注入，不能只写到项目 `.env`。unit 固定了 PATH/NO_PROXY，弥补直接 Uvicorn 启动不走桌面 CLI PATH 补全的差别。需要额外 CLI 路径时在 `/etc/chirpiva/environment` 明确添加。模型密钥通过主机私有配置供应，不写进源码归档、日志或公共网页。

2026-11-06 用户明确选择全部执行工具自动授权，本部署启用 `EASEL_AUTO_ALLOW_AI_TOOLS=1`。原生命令、文件和额外执行权限请求按当前回合自动处理，设置页显示实际生效模式；保留线程/回合关联、角色检查与撤权终止。它不是把操作系统账户提升为 root，也不把工具授权记录冒充制作、发布的人工业务审批。修改该进程设置后，先等待当前回合结束再重启 Web；旧对话下一轮继续沿用原历史。

聊天默认使用简洁模式，运行中及完成后的工具详情都保持收起；用户可手动展开或选择详细模式。关键业务结果保留简短状态和操作入口，原始命令、stdout/stderr、JSON 放在详情中。

Caddy 配置先独立校验；若目标机已有其他网站，合并站点块，不覆盖已有配置。域名 DNS 的 apex 与 www 均需正确指向目标，错误 AAAA 记录需要修正，不能保留指向别处的 IPv6。核对账户初始化和切换条件完成后再启用站点。

```bash
sudo caddy validate --config /srv/chirpiva/app/deploy/chirpiva/Caddyfile --adapter caddyfile
# 无其他站点时才能把本文件安装为 /etc/caddy/Caddyfile。
# 有既有站点时保留原配置，在其中 import 这两个站点块。
```

Caddy 通过域名自动申请/续签 TLS，www 跳转到 apex；保留 Host 和代理 scheme，SSE 立即刷新。`/api/auth/setup` 在公网反代层明确拒绝。Uvicorn 只信同机 127.0.0.1 的转发头；不要改为 `*`，不要把 7860 直接暴露公网。

### 性能配置与独立工作进程

`performance.caddy` 必须安装为 `/etc/caddy/chirpiva-performance.caddy`，在现有站点的 Web 代理位置引入，保留已有私有路由。React `dist/assets` 和 Nuxt `.output/public/_nuxt` 的构建产物分别同步到 `/srv/chirpiva-public/assets`、`/srv/chirpiva-public/huobao-studio/_nuxt`。该树只允许公开构建文件，目录 0755、文件 0644；不要给 Caddy 增加读取应用凭据、私有产物或浏览器资料的组权限。先运行 `precompress-assets.mjs` 生成 Brotli/gzip，保留原文件与旧版本的哈希 chunk；页面入口只在后端健康后原子切换。

原生 Agent、视频引擎和媒体转换通过服务用户的 systemd scope 分离，避免与 Web 共用内存回收预算。安装 `chirpiva-workloads.slice` 到 `/srv/chirpiva/.config/systemd/user/`，开启 `loginctl enable-linger chirpiva`，以该用户的 `XDG_RUNTIME_DIR=/run/user/1001`、`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus` 重载 user manager（实际 UID 以主机为准）。在生产开启 `EASEL_WORKLOAD_ISOLATION=1` 前，验证 scope 的 UID、stdin/stdout、进程组取消和内存/CPU上限；不支持 user systemd 的环境保持关闭。`chirpiva-resource-limits.conf` 是 Web 本身的资源上限，不能误当工作负载 slice 安装。

视频播放使用私有、按内容版本缓存的 faststart 副本；原画默认无损封装，流畅预览单独转码，下载仍返回原文件。媒体缓存只在认证与 IP 权限通过后允许私有 ETag 重验证，不能加入公共静态树。大工具记录仍完整存储，页面和 SSE 只按需投影摘要，展开时读取同用户/IP的原始详情。

`GET /api/performance/summary` 供管理员读取本 IP 的 p50/p95。客户端指标只上报固定类别、耗时和字节数，不采集提示词、媒体 URL 或凭据；视频生成的阶段耗时记录用于区分排队、模型生成、下载与交付，不代表外部模型耗时能由页面优化消除。

## 4. 首次安全验证与数据切换

应用没有完整“禁用后台任务”开关。启动 lifespan 会恢复运营、SuperAgent 和互动任务，某些 API 也能懒启动运营引擎。不能把完整生产快照复制到第二台机器后随意启动，也不能把 `--lifespan off` 当成安全维护模式。

首次部署先用**空数据、无外部凭据**的固定路径验证 Linux 安装。只通过本机/SSH 隧道检查主页、bootstrap、静态文件和匿名拒绝；不调用运营或生成接口。可以短时运行正常单进程应用，但不接模型/平台凭据、不导入任务状态。验收结束先停止它，归档测试数据，确保迁移目标为空。

```bash
# 在本地电脑建立隧道；替换为实际已验证主机别名。
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:7870:127.0.0.1:7860 DEPLOY_HOST
```

本地 7870 应先确认空闲；该端口已在应用默认允许的 Origin 中，可避免占用仍运行旧站的 7860。首次管理员通过隧道的 `http://127.0.0.1:7870` 建立；若迁移现有 `.easel` 则沿用现有身份，不另造管理员密码，不从公网放开 setup。验证 fresh bootstrap 后产生的临时身份不要混入真实迁移数据。

### 迁移清单

本机测得的概况仅供容量规划：projects 约 1.7 GiB，outputs 约 6.9 GiB，`.easel` 约 436 MiB，外部 host-state 约 55 MiB，默认浏览器状态约 288 MiB。实际迁移前重新统计。

- 仓库 `.easel`：用户、密码哈希与会话等；应保护备份并考虑在正式切换后撤销旧会话。
- `projects`、`outputs`、用户 `profiles` 与相关 `assets`：IP、画像、内容、会话与任务数据；不能只拷贝已经生成的媒体。
- 仓库外 Host 状态：发布批准、视频生成、火宝数据与相关收据。
- 默认及各 IP 浏览器登录态、原生 Agent 配置：分开核对，不盲目搬运 macOS 加密 cookie、个人订阅登录或不可移植绝对路径。
- 模型/PPU 连接、SSH 隧道和代理：云机需有经过验证的私网或受保护网络路径。源 Mac 的 localhost 地址不能直接放进云机配置。

正式切换步骤：

1. 列出现有运营/制作/发布任务及原生子进程，确认可安全停止的时机；保留在途任务 ID，不把“未知”当失败重投。
2. 停止源端调度及会写上述数据库的相关进程，确认没有第二个发布或互动调度者。暂停自动拉起旧实例的 launch agent/service；不要误停无关训练/模型服务。
3. 做一致性、受保护的备份。SQLite 使用其备份机制或在全部写入进程停止后拷贝完整文件组；不可对活跃库只复制 `.sqlite3` 而遗漏 WAL。
4. 将数据恢复到固定路径及服务账户，重建 `projects/*` 中指向源 Mac 的工作区软链接。逐个检查所有绝对路径及专用 sidecar 运行记录；不把旧 PID、旧 Node/FFmpeg 路径当成云端有效状态。
5. Host 状态目标根为 `/var/lib/chirpiva/easel/host-state/<hash>`，其中 hash 是 `sha256('/srv/chirpiva/app')[:24]`。只迁移本仓库对应的源哈希子树到该目标，保持 IP 子目录/批准证据原样，不改写批准与业务状态。源与目标哈希不同是正常现象。
6. 按 Linux 重建火宝运行时、原生 CLI 和所需浏览器会话；配置网络与模型后先检查连通性，不提交新的付费测试任务。
7. 确认源端已停止后，才在云端启动唯一实例；验证下列闭环后再切 DNS/启用公网站点。
8. 回滚必须先停云端写入，再恢复或协调最新数据及收据，最后才允许源端启动。不可让两端同时消费相同任务。

## 5. 上线验收

满足切换条件后才执行 `systemctl start chirpiva`，验收后才 `systemctl enable chirpiva` 并启用/重载 Caddy。不能把以下清单直接标为通过：

1. `web/frontend/dist/index.html` 与对应 assets 存在；HTTPS 加载真实 React 页面。仅首页 200 不够，应用可能静默退回旧页面。
2. HTTP 跳 HTTPS、www 跳 apex、证书和 DNS 正确；公网访问 7860/副服务端口失败。
3. 未登录业务 API 返回 401；公网 setup 返回 403；不同用户/IP 数据隔离；登录 cookie 包含 Secure、HttpOnly、SameSite=Strict；跨来源写入被拒绝。
4. 真实选定的 Agent 可对话，SSE 正常渐进显示，关闭页面不错误丢失任务；新建与恢复会话正常。
5. 老白等既有 IP、六维画像、已有内容和必要私有 Host 收据可正确读回，数量与备份一致。
6. 火宝编辑器、素材下载及 FFmpeg 本地合成可运行；模型连通性和能力报告真实。付费生成、真人听音/口型验收不因部署自动获得批准。
7. 社交账号在云机上重新验证身份/登录，发布与互动不自动发送测试内容；实际公开动作沿用用户授权。
8. 受控重启后身份、数据、任务状态和证书持久化正常，检查源实例仍未重启。

官方配置依据（2026-11-06 核对）：[Uvicorn settings](https://uvicorn.dev/settings/)、[Uvicorn lifespan](https://uvicorn.dev/concepts/lifespan/)、[Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)、[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)。
