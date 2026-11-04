# 海外账号登录

账号管理支持 Facebook、Instagram、TikTok、X（Twitter）、YouTube 的浏览器会话登录。

1. 打开当前 IP 的「账号管理」，选择平台并点击「登录」。
2. 在运行 Easel 的电脑上弹出的独立浏览器中，直接在平台官网输入账号密码，并完成平台要求的验证。
3. Easel 检测到已登录页面后关闭登录窗口，保存会话，再更新账号卡片。
4. 之后可「校验账号」；「退出登录」清除当前 IP 下该平台的本地会话。

这次接入的范围是账号会话管理。海外平台的一键发布、官方 OAuth API 权限和运营数据采集尚未接入。
网页结构和平台访问策略可能变化，完整真实账号登录仍需由用户完成验证；页面加载失败、验证码或安全检查不会被记为登录成功。

## 运行条件

- Easel 所在电脑有图形桌面，且能访问对应平台。如果从另一台电脑访问 Easel，登录窗口仍会在服务器电脑上打开。
- 已安装 Playwright 的 Chromium：`python -m playwright install chromium`。
- 海外浏览器保留系统网络配置；也可在项目 `.env` 中配置 `EASEL_SOCIAL_PROXY=http://127.0.0.1:7890`。修改后重新发起登录即可。
- 代理优先级：`EASEL_SOCIAL_PROXY` → `EASEL_PROXY` → `https_proxy` / `HTTPS_PROXY` → `all_proxy` / `ALL_PROXY` → 系统网络。

平台账号不需要在 Easel 内填写开发者 App ID、App Secret、密码或验证码。会话保存在当前 IP 的独立浏览器目录，包含敏感登录信息，请勿分享或提交到 Git。

默认 IP 使用 `~/.easel-browser-profiles/<平台>Profile`；其他 IP 使用 `projects/<IP ID>/browser/<平台>Profile`。
同一目录不能被两个浏览器同时打开。检测到登录成功后，先关闭并保存浏览器，再发布成功状态。

状态和经过脱敏的错误保存在当前 IP 的 `outputs/_login/<平台>.json`。
遇到网络限制、登录失败或超时，可关闭弹窗、检查平台页面和网络，再重新登录。
