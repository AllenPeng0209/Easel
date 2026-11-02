# 互动中心平台通道

互动中心的模拟通道只在本机保存消息与回执，不调用真实平台。当前真实通道适配器是独立、可测试的接入层，不代表已完成任何账号授权或真实收发验收。

| 通道 | 当前能力 |
| --- | --- |
| 抖音评论官方接口 | 已有凭据的企业号可接入评论列表及按精确评论 ID 回复；需要运营方确认实际准入与授权 |
| 小红书评论人工通道 | 用客户独立浏览器登录态导入指定笔记评论；真实发送保持人工处理 |
| 小红书／抖音私信 | 等待对应平台准入、授权与回调接入；当前不声称已连接 |
| 模拟评论／私信 | 后端本机验收，不读取真实账号，不发送外部消息 |

## 回调接口

`easel.interaction_channels` 导出：

```python
channel_capabilities(channel=None)
sync_channel(channel, source_url='')
send_reply(channel, conversation, draft, operation_id)
```

能力返回 `state/can_sync/can_send/message`。配置被读取仅称为 `configured`，不是已验证连接。发送回执包含 `status`（`sent`、`failed`、`unknown`）、`external_message_id`、`receipt_id`、`message` 和 `mode`。只有平台明确返回成功及回复 ID 才能认定 `sent`；超时、无明确 ID、网关异常均保留未知结果，不自动重发。

## 抖音评论

通道选择 `platform=douyin`、`kind=comment`、`delivery_mode=official`。`config.env_prefix` 必须以 `EASEL_INTERACTION_` 开头，只可包含大写字母、数字和下划线。服务进程环境中使用此前缀的下列键：

- `<前缀>_ACCESS_TOKEN`：该应用、账号实际授权的 token。
- `<前缀>_OPEN_ID`：对应授权账号。
- `<前缀>_VIDEO_ID`：对应的开放平台视频 ID；不是随意拼出的网页视频编号。
- `<前缀>_PERMISSION_VERIFIED=true`：运营方已在平台后台核实权限的显式声明，不是绕过平台权限的标记。

账号和视频 ID 也可分别由非机密通道配置 `account_id`、`video_id` 提供。凭据不写入通道 JSON 或浏览器返回内容。请求原点固定为 `https://open.douyin.com`，不接受自定义 API 地址，不跟随跳转，不继承系统 HTTP 代理。

当前官方[企业号评论列表文档](https://open.douyin.com/platform/resource/docs/openapi/enterprise-open-ability/comment-management-enterprise/comment-list/)注明内测名额已满、暂停申请。必须先确认现有账号／应用的实际权限，不能把“有企业号”当成已获得接口授权。[回复评论文档](https://open.douyin.com/platform/resource/docs/openapi/enterprise-open-ability/comment-management-enterprise/video-comment-reply/)要求 `video.comment` 权限与用户授权。

## 小红书评论导入

仅 `delivery_mode=manual` 明确启用浏览器读取；官方通道不会静默回退为浏览器自动化。使用固定的 `skills/shared/scripts/xhs_comment.py fetch`，完整 HTTPS 笔记链接限定于小红书官方域名。访问参数经私有 stdin 传入子进程，不出现在进程命令行中；返回消息使用去掉查询参数的笔记地址。

浏览器登录态默认位于：

```text
.scratch/interaction_profiles/<项目ID>/<客户ID>/<通道ID>/XiaohongshuProfile
```

如设置服务环境 `<前缀>_PROFILE_BASE`，仍会在该根目录下追加项目 ID、客户 ID 与通道 ID，避免不同项目中相同客户编号或多个账号通道复用登录态。操作员可用已有发布脚本登录到这份指定目录：

```bash
.venv/bin/python skills/shared/scripts/xhs_publish.py login \
  --profile-base /absolute/path/to/interaction_profiles/PROJECT_ID/CLIENT_ID/CHANNEL_ID
```

本轮没有执行上述登录或任何平台收发。现有评论发送脚本按昵称定位，缺少可验证的精确评论目标及发送回执，因此本接入层不会调用它并冒称已发送。小红书官方私信能力参见[官方开放平台](https://ad-market.xiaohongshu.com/)，具体准入需单独核对。

## 验证

```bash
.venv/bin/python -m pytest tests/test_interaction_channels.py -q
```

测试使用离线 HTTP transport 和受控进程执行器，覆盖权限缺失、精确评论 ID、平台业务失败、未知发送结果不重试、跨项目浏览器隔离、私有临时文件、URL 参数不进入 argv 和错误信息不泄露凭据。它们不替代真实账号接入验收。
