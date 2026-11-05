# 独立数据采集器接入

`easel/trends` 保留为 Python 采集库，可在命令行脚本中使用 Python、HTTP JSON 和 RSS/Atom 数据源。选题策划、热点雷达页面及其 Web 管理接口已移除；这里说明采集库和协议的独立使用方式。

六个内置平台（微博、抖音、知乎、B站、百度、头条）使用 `easel/trends/builtin.py` 的 Python 子进程协议。自定义网站、数据库或浏览器采集由自己的采集器实现，库负责调用、校验、去重和缓存。

## 1. 在命令行运行 Python 采集器

从项目根目录执行示例（该例只输出演示条目，不访问真实平台）：

```bash
.venv/bin/python examples/trends/python_collector.py
```

需要从配置调用时，可复制 `config/trend-sources.example.json` 为 Git 忽略的 `config/trend-sources.json`，再用下文第 6 节的 Python 接口读取。已有配置时合并所需条目，保留现有来源。

也可以通过环境变量指定独立配置文件：

```bash
export EASEL_TREND_SOURCES_CONFIG=config/trend-sources.example.json
```

路径相对于项目根目录，也支持绝对路径。显式指定但文件不存在时会报错。调用方将配置所需的环境值传给采集库；采集库不会自动启动 Web 服务或维护页面状态。

## 2. Python 数据源配置

```json
{
  "version": 1,
  "include_builtin": true,
  "sources": [
    {
      "id": "my-news",
      "label": "我的行业资讯",
      "type": "python",
      "script": "../collectors/news.py",
      "python": "../collectors/.venv/Scripts/python.exe",
      "args": ["--category", "technology"],
      "env": {"NEWS_API_KEY": "${MY_NEWS_TOKEN}"},
      "default": true,
      "timeout_seconds": 20,
      "refresh_seconds": 300,
      "max_stale_seconds": 86400
    }
  ]
}
```

规则：

- `script` 和带路径分隔符的 `python` **相对配置文件所在目录**解析，支持绝对路径；`python` 也可以是 PATH 中的解释器名称。省略 `python` 时使用启动 响芽 的解释器。Linux/macOS 的虚拟环境通常为 `.venv/bin/python`。
- 每个数据源可以使用独立虚拟环境。请自行安装依赖，响芽 不会在查询时执行 pip 或修改自身依赖。
- 脚本工作目录为脚本所在目录。执行形式为 `python -X utf8 script.py ...args`，不经过 shell，不接受 shell 命令串。
- 脚本默认只继承运行、代理和证书相关变量，不再继承整个后端环境。用 `env` 显式传入需要的变量；`${NAME}` 可引用后端环境或项目 `.env` 中的值，未设置会返回 `missing_environment`。不会执行变量值中的命令，也不递归展开变量。聊天 API Key 等无关凭据不会自动传给脚本。
- 响芽 注入 `EASEL_TREND_SOURCE_ID`，脚本可以读取当前数据源 ID。查询条数不会作为脚本参数注入：采集结果先缓存，展示时再截取。
- stdout 必须只有一份完整 UTF-8 JSON；日志写 stderr。退出码 0 表示成功，非零表示失败。**真实采集失败应抛异常或非零退出，不要伪装成 `items: []`**。
- 脚本按需启动，输出后退出。HTTP 查询不会等待常驻脚本提供接口；需要长期保持登录态或复用浏览器时，使用下节的 HTTP 服务。

## 3. HTTP 数据源配置

运行可直接测试的本地示例（保持此终端运行）：

```powershell
python -X utf8 examples/trends/http_service.py --port 8766
```

将 `config/trend-sources.json` 中 `my-http` 的 `enabled` 改成 `true`，再次用 Python 接口读取该源即可。响芽 只请求服务，不负责启动或守护 HTTP 服务。

已有采集服务可按下面方式接入：

```json
{
  "id": "team-news",
  "label": "团队行业热点",
  "type": "http",
  "url": "https://collector.example.com/trends",
  "method": "POST",
  "headers": {"Authorization": "Bearer ${TEAM_TREND_TOKEN}"},
  "body": {"category": "technology", "limit": 100},
  "default": true,
  "timeout_seconds": 10,
  "refresh_seconds": 600,
  "max_stale_seconds": 86400
}
```

- 将这个对象放入配置的 `sources` 数组。支持 GET（默认）和 POST；POST 的 `body` 按 JSON 编码，GET 不允许带 body。
- 响应需为 HTTP 200 类成功状态，响应体是下节的 v1 JSON，不再额外包装 `data`。`http` 类型不接收 HTML 或 XML；RSS/Atom 请使用 `rss` 类型。
- URL 仅允许 HTTP(S)，不能包含 `user:password@host`。鉴权使用 `headers`，凭据建议保存在 `.env` 或环境变量，通过 `${NAME}` 引用。
- HTTP 3xx 重定向视为失败，不会跟随跳转或把鉴权头转发到其他地址；配置最终服务地址。
- 支持内网和 localhost；默认绕过 localhost 代理。请求遵循 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`，没有对应代理时使用 `EASEL_PROXY`。HTTP 源也可通过自己的 `env` 设置覆盖代理变量。
- 本地示例可用 `DEMO_TREND_TOKEN` 开启 Bearer 鉴权；随后配置 `Authorization: Bearer ${DEMO_TREND_TOKEN}`。示例仅用于开发验证，生产服务的部署与访问控制由服务维护者负责。

### 直接订阅 RSS / Atom

无需写脚本或部署转换服务，在 `sources` 数组中增加：

```json
{
  "id": "industry-feed",
  "label": "行业订阅",
  "type": "rss",
  "url": "https://your-site.example/feed.xml",
  "headers": {},
  "default": true,
  "timeout_seconds": 10,
  "refresh_seconds": 900,
  "max_stale_seconds": 86400
}
```

- 支持 RSS 2.0、RSS 1.0（RDF）和 Atom 1.0；使用 GET 请求，和 HTTP 数据源共用鉴权头、环境变量、代理、超时和响应大小限制。示例配置里的 `my-rss` 默认禁用，请替换真实订阅地址后启用。
- RSS `guid` / Atom `id` 转为稳定 ID，标题转为 `title`，文章链接转为 `url`，RSS `pubDate` / Dublin Core `date` / Atom `published`（无值时 `updated`）转为 `published_at`。
- 相对链接与 Atom `xml:base` 会解析成绝对地址；无文章链接但 RSS guid 是永久 HTTP(S) 地址时可作为链接。非 HTTP(S) 链接不会成为可点击地址。
- 发布时间缺失、无时区或无法识别时保留为 null；没有标题的条目跳过。没有热度的订阅保持空热度，不人为生成排名分数。保留 Feed 原有顺序，最多接收前 500 条有效条目。
- `fetched_at` 是 响芽 本次成功读取订阅的时间，文章本身的新旧看 `published_at`。Feed 空列表属于采集成功，无法解析的 XML 属于失败，会按缓存规则回退。
- 不执行 Feed 中的 HTML，也不加载 DTD 或外部实体；没有额外安装依赖。与 HTTP 源相同，不自动跟随重定向，请填写最终订阅地址。

仓库也提供了 [演示 Feed](../examples/trends/feed.xml)，可以完全在本地验证：

```powershell
python -m http.server 8767 --bind 127.0.0.1 --directory examples/trends
```

将示例配置 `my-rss` 的 `url` 改为 `http://127.0.0.1:8767/feed.xml`，并设置 `enabled: true`。再次用 Python 接口读取该源即可得到一条演示订阅。演示文章的发布时间是固定值，采集时间是本次读取时间。

## 4. 统一数据协议 v1

Python stdout 与 HTTP 响应体完全一致：

```json
{
  "version": 1,
  "fetched_at": "2026-10-29T10:00:00+08:00",
  "items": [
    {
      "id": "topic-123",
      "title": "热点标题",
      "url": "https://example.com/topics/123",
      "published_at": "2026-10-29T09:00:00+08:00",
      "heat": {"value": 123456, "text": "12.3万", "unit": "阅读"}
    }
  ]
}
```

| 字段 | 规则 |
|---|---|
| `version` | 必填，整数 `1` |
| `fetched_at` | 必填，带时区的 ISO 8601 采集时间；上游有缓存时填写真实采集时间，不能用接口请求时间冒充；比当前时间超前 5 分钟以上会被拒绝 |
| `items` | 必填数组，允许为空，最多 500 条；输出顺序就是榜单顺序 |
| `items[].id` | 可省略；建议提供数据源内稳定 ID。省略时按 URL（无 URL 则标题）生成稳定哈希；同一数据源内重复 ID 保留第一条，不跨源去重 |
| `title` | 必填非空字符串，最长 1000 字符 |
| `url` | 可省略、空字符串或 null；有值时必须是无内嵌凭据的 HTTP(S) 链接 |
| `published_at` | 可省略或 null；有值时必须为带时区的 ISO 8601 时间 |
| `heat` | 可省略；对象中 `value` 是有限数值或 null，`text` / `unit` 为字符串，均可省略 |

`heat.text` 用于展示；缺失时展示 `heat.value`。不同平台的 `value` 和 `unit` 含义可能不同，响芽 不做跨平台热度排名。额外字段会被忽略；已定义字段类型错误时整批拒绝，避免错误结果污染缓存。

最小合法输出：

```json
{"version": 1, "fetched_at": "2026-10-29T02:00:00Z", "items": [{"title": "我的热点"}]}
```

此处时间仅为格式示例，运行时请生成真实时间。完整实现见 [Python 示例](../examples/trends/python_collector.py) 和 [HTTP 示例](../examples/trends/http_service.py)。

## 5. 数据源管理与缓存

| 配置项 | 默认值 / 说明 |
|---|---|
| `version` | 配置根对象必填，整数 `1` |
| `include_builtin` | 默认 `true`；设为 `false` 只使用自定义源 |
| `id` | 小写字母、数字、下划线、连字符，1–64 字符，首字符须为字母或数字 |
| `label` | 展示名称，必填，最多 80 字符 |
| `enabled` | 默认 `true`；设为 `false` 禁用源，只需提供 `id` 和 `enabled` |
| `default` | 自定义源默认 `true`；供调用方选择默认来源 |
| `timeout_seconds` | 默认 20，范围 0.1–120 秒；采集工作进程有额外 2 秒启动/清理预算，超时终止 |
| `memory_mb` | 默认 256，范围 64–512 MiB；Windows 为整个采集进程树的提交内存上限，Linux 同时限制地址空间和进程组 RSS，macOS 使用进程组 RSS 看门狗（不依赖不可靠的 RLIMIT_AS） |
| `refresh_seconds` | 默认 300，范围 1–86400 秒；按需查询的缓存间隔，不是后台定时任务 |
| `max_stale_seconds` | 默认 86400，不能小于刷新间隔，最多 604800；按 `fetched_at` 计算结果可保留多久 |

`sources` 最多 64 项，不允许重复 ID。自定义项与内置同 ID 时**完整替换**该项配置；内置 ID 为 `weibo`、`douyin`、`zhihu`、`bilibili`、`baidu`、`toutiao`。例如：

```json
{
  "version": 1,
  "include_builtin": true,
  "sources": [{"id": "weibo", "enabled": false}]
}
```

内置源将微博、抖音、知乎标记为默认来源。调用方通过 `load_sources()` 获取启用列表。配置、脚本或环境变量变化会使对应缓存失效。缓存只存在当前 `TrendService` 实例中，重新创建实例会清空缓存。

同一源并发请求复用采集，一个服务实例同时最多执行 4 个采集器。超额请求短暂等待后返回 `busy`，由调用方稍后重试，不会无界排队。普通失败最多等待 30 秒重试（刷新间隔更短则遵循刷新间隔）；忙状态 1 秒后可重试。stdout、stderr 和 HTTP 响应各限制为 2 MiB，超限拒绝。不要输出大量日志。配置文件最多 128 KiB，内存缓存按 16 MiB 序列化预算和最多 96 项限制淘汰。

接口状态：

- `fresh`：成功且数据未过期；空数组也属于成功。
- `stale`：数据超过刷新间隔，或本次采集失败，暂时使用仍在最大保留时间内的旧结果。返回真实 `fetched_at`，调用方应标明过期缓存。
- `error`：没有可用结果或已超过最大保留时间；返回空列表和明确的错误码。

响芽 不把脚本 stderr、HTTP 响应错误正文或原始异常透传给调用方，避免泄露鉴权信息。排错时可用同一解释器、参数、环境在本地直接运行脚本。

## 6. Python 查询接口与边界

下面的完整示例直接读取示例配置，仅运行 `my-python` 演示来源，不改本机已有配置：

```bash
.venv/bin/python - <<'PYCODE'
import json
import os
from pathlib import Path
from easel.trends.sources import load_sources
from easel.trends.service import TrendService

root = Path.cwd()
environment = dict(os.environ)
environment["EASEL_TREND_SOURCES_CONFIG"] = "config/trend-sources.example.json"
warnings = []
sources = load_sources(root, environment, warnings=warnings)
source = next(item for item in sources if item.id == "my-python")
result = TrendService().get(source, environment, limit=15)
print(json.dumps({"result": result, "warnings": warnings}, ensure_ascii=False, indent=2))
PYCODE
```

`load_sources(root, environment)` 返回当前启用的 `Source` 对象；`TrendService.get(source, environment, limit)` 返回该源的一组结果，`limit` 限制在 1–30。要复用缓存，在同一进程中复用同一个 `TrendService` 实例。

结果包含 `platform`、`label`、`items`、`ok`、`status`、`stale`、`fetched_at`、`last_attempt_at` 和 `error`。条目保留 `hot` 并提供 `id`、`heat`、`published_at`。`ok` 表示存在未超最大保留时间的成功结果，成功空数组也是 true。单源失败不影响其他来源；传入 `warnings` 时，单条无效配置会被跳过并记录原因。

网络解析和 Python 采集由短生命周期工作进程执行。Windows 使用 Job Object；Linux/macOS 使用进程组、资源限制和进程树监测。脚本仍以调用者账号运行，这些限制并不是文件系统、网络或系统调用沙箱。协议解析拒绝过深 JSON/XML、重复 JSON 字段、非有限数值、异常 Unicode、超长字段及不安全链接。

## 7. 使用真实公开数据验证

[真实数据配置](../config/trend-sources.live.example.json) 包含四条接入路径，仍保留全部内置平台：

| 接入路径 | 数据来源 | 示例 |
|---|---|---|
| Python 子进程 | Hacker News 首页榜单，Algolia 官方 HN Search API | `live_collector.py --provider hackernews --limit 20` |
| HTTP JSON v1 | 本地 HTTP 适配服务每次实时请求 GitHub CLI 公开 Releases API | `live_http_service.py --provider github --limit 10` |
| RSS 2.0 | BBC News 官方订阅 | `https://feeds.bbci.co.uk/news/rss.xml` |
| Atom 1.0 | GitHub CLI Releases 官方 Atom | `https://github.com/cli/cli/releases.atom` |

Python 与 HTTP 两个脚本都在 `examples/trends/`，只有标准库依赖，成功结果均由本次上游请求生成，没有内置固定新闻或读取离线快照。HTTP 适配器不是一个现成的第三方 v1 服务：它的作用是把 GitHub 的原始 Releases JSON 转成 响芽 v1，验证“外部服务自行采集、平台只消费结构化数据”的完整路径。

先在独立终端启动 HTTP 服务，并保持运行：

```powershell
python -X utf8 examples/trends/live_http_service.py --provider github --limit 10 --port 8766
```

上游请求使用该终端的 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 环境变量；它是独立服务，不自动读取 响芽 的 `.env`。如果网络需要代理，请在这个终端按实际情况设置标准代理变量。响芽 启动的 Python 子进程则会继承平台传入的环境设置。

没有现有配置时，可复制真实数据配置：

```powershell
Copy-Item config/trend-sources.live.example.json config/trend-sources.json
```

若已有自定义配置，请把四项合并进现有 `sources` 数组，保留原有设置。接入条目无需填 API Key；匿名公开 API 仍可能受上游限流或网络影响。此配置用于复现接入验证，不代表平台承诺这些公开服务永久可用。

验证时新建 `TrendService` 实例，再逐个调用 `get(source, environment, limit=30)`。只有 `status: fresh`、`stale: false`、`error: null` 且含真实条目，才记作本次采集通过；不要把旧缓存回退记作上游正常。

自动测试只调用本地回环服务与合成样本，不请求真实模型或平台。手工真实来源验证与自动回归分开执行；公开示例不自动启用。跨平台回归覆盖 Windows、Linux、macOS 及 Python 3.10 / 3.12。
