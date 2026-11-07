"""Audited QR preview adapters, separate from account authentication.

Selectors are adapted from social-auto-upload (MIT, Copyright 2023 dreammis),
commit 3847fff01f5434a8a32829833163cd6723cabc59, and xiaohongshu-mcp
(Apache-2.0, Copyright 2025 xpzouying), commit
3b352aa075d63cd5ebae546c9fe7d7d9b81fa7c9. Local compatibility selectors come
from the existing Xiangya login runners. Sources and licenses are retained in
vendor/login-qr-sources/. Modified for strict origins, bounded QR leaves,
explicit refresh, and per-session ownership; this does not verify an account.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit


_LOGIN_TEXT = (r'扫码登录', r'扫描二维码', r'扫码', r'Scan.*QR', r'QR.*code')
_EXPIRED_TEXT = (r'二维码(?:已)?(?:失效|过期|超时|无效)', r'请刷新二维码', r'QR.*(?:expired|invalid)')
_SCANNED_TEXT = (r'扫码成功', r'扫描成功', r'已扫码', r'请在手机.*确认', r'请在(?:微信|知乎APP)中确认')
_VERIFICATION_TEXT = (r'安全验证', r'身份验证', r'短信验证', r'拖动.*滑块', r'完成.*验证', r'Security verification')
_REFRESH_TEXT = (
    r'^(?:二维码(?:已)?(?:失效|过期)\s*[,，。]?\s*)?(?:点击刷新|刷新二维码|重新获取二维码)$',
    r'^二维码(?:已)?(?:失效|过期)$',
)


@dataclass(frozen=True)
class QRPlatform:
    hosts: tuple[str, ...]
    selectors: tuple[str, ...]
    login_text: tuple[str, ...] = _LOGIN_TEXT
    expired_text: tuple[str, ...] = _EXPIRED_TEXT
    scanned_text: tuple[str, ...] = _SCANNED_TEXT
    verification_text: tuple[str, ...] = _VERIFICATION_TEXT
    refresh_text: tuple[str, ...] = _REFRESH_TEXT
    refresh_selectors: tuple[str, ...] = ()
    expired_selectors: tuple[str, ...] = ()
    frame_paths: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    root_paths: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self):
        for key in ('frame_paths', 'root_paths'):
            object.__setattr__(self, key, MappingProxyType({host: tuple(paths) for host, paths in getattr(self, key).items()}))

    def asdict(self) -> dict:
        """Return a detached JSON-compatible transport view, never mutable policy."""
        result = {item.name: getattr(self, item.name) for item in self.__dataclass_fields__.values()}
        result['frame_paths'] = dict(self.frame_paths)
        result['root_paths'] = dict(self.root_paths)
        return result

    def allows_url(self, url: str, *, frame: bool = False) -> bool:
        if not isinstance(url, str) or len(url) > 16384:
            return False
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443)):
                return False
        except ValueError:
            return False
        if frame:
            prefixes = self.frame_paths.get(parsed.hostname, ())
            return any(parsed.path == prefix or parsed.path.startswith(prefix.rstrip('/') + '/') for prefix in prefixes)
        if parsed.hostname not in self.hosts:
            return False
        prefixes = self.root_paths.get(parsed.hostname, ())
        return not prefixes or any(parsed.path == prefix or parsed.path.startswith(prefix.rstrip('/') + '/') for prefix in prefixes)


_PLATFORMS: Mapping[str, QRPlatform] = MappingProxyType({
    'douyin': QRPlatform(
        hosts=('creator.douyin.com',),
        selectors=(
            'div#animate_qrcode_container img[src^="data:image"]',
            'div[class*="animate_qrcode_container"] img[src^="data:image"]',
            'div[class*="scan_qrcode_login_content"] img[src^="data:image"]',
            'img[aria-label="二维码"]',
        ),
    ),
    'xiaohongshu': QRPlatform(
        hosts=('www.xiaohongshu.com', 'creator.xiaohongshu.com'),
        selectors=('.login-container img.qrcode-img', 'img.qrcode-img',
                   '.sso-login-wrapper [class*="qrcode"] img',
                   '.sso-login-wrapper [class*="qrcode"] canvas'),
    ),
    'kuaishou': QRPlatform(
        hosts=('cp.kuaishou.com', 'passport.kuaishou.com'),
        selectors=('main#login-form div.qr-login img[alt="qrcode"]',
                   'main#login-form [class*="qrcode"] img',
                   'main#login-form [class*="qrcode"] canvas'),
        expired_selectors=('div.qrcode-status.qrcode-status-timeout',),
        refresh_selectors=('main#login-form p.qrcode-refresh',),
    ),
    'weixin-channels': QRPlatform(
        hosts=('channels.weixin.qq.com',),
        selectors=('div.login-qrcode-wrap img.qrcode', 'div.qrcode-wrap img.qrcode',
                   'img.qrcode', 'img.js_qrcode_img', 'img.web_qrcode_img'),
        frame_paths={'open.weixin.qq.com': ('/connect/qrconnect',)},
    ),
    'wechat-oa': QRPlatform(
        hosts=('mp.weixin.qq.com',),
        selectors=('img.login__type__container__scan__qrcode',
                   '.login__type__container__scan img[src*="qrcode"]'),
    ),
    'zhihu': QRPlatform(
        hosts=('www.zhihu.com',),
        selectors=('img.Qrcode-img', '.SignFlow-qrcode img', '.SignFlow-qrcode canvas',
                   '.SignFlowQRCode img', '.SignFlowQRCode canvas'),
        refresh_selectors=('.SignFlow .Qrcode-mask',),
    ),
    'juejin': QRPlatform(
        hosts=('juejin.cn',),
        selectors=('img.qrcode-img', '.login-panel [class*="qrcode"] img',
                   '.login-panel [class*="qrcode"] canvas'),
    ),
    'toutiao': QRPlatform(
        hosts=('mp.toutiao.com',),
        root_paths={'mp.toutiao.com': ('/auth/page/login',)},
        selectors=('img[src*="qrcode"]', 'img[src*="qr_code"]',
                   '[class*="qrcode"] img', '[class*="qr-code"] img', 'canvas[class*="qr"]'),
    ),
})


def get_qr_platform(platform: str) -> QRPlatform | None:
    """Select DOM rules; remote ownership still matches its exact platform key.

    Native AccountConnections uses ``xhs`` while the public catalog uses
    ``xiaohongshu``. Only this known display/adapter distinction is normalized;
    this helper grants no session access and must not normalize registry keys.
    """
    if not isinstance(platform, str):
        return None
    return _PLATFORMS.get('xiaohongshu' if platform == 'xhs' else platform)
