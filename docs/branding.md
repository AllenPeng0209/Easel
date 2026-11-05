# 响芽 Xiangya 品牌与兼容约定

中文品牌名为 **响芽**，英文名为 **Xiangya**，命令名为 **`xiangya`**。
产品定位是 **AI 自媒体自动化运营平台**，覆盖选题、创作、发布和运营复盘。

```bash
xiangya web
xiangya doctor
python -m xiangya --help
```

安装包发布名为 `xiangya`。旧 `easel` 命令与 Python 模块继续转接同一运行时。
已有配置、账号、IP、会话及制作任务直接沿用，安装或升级时无需重建。

兼容标识包含 `EASEL_*` 环境变量、`.easel/` 数据目录、
`.openclaw-easel/` 引擎配置、浏览器账号目录、存储键、HTTP 头和既有任务标记。
这些标识属于数据与接口协议，保持稳定以便原有记录及外部接入继续工作。
原有 Git 仓库地址和上游来源链接继续指向实际存在的仓库。

浅色与深色字标分别位于 `../assets/readme/xiangya-brand.svg` 和
`../assets/readme/xiangya-brand-dark.svg`；站点图标使用
`../web/static/xiangya-icon.svg`。
