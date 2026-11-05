"""Operator-configured direct PPU transport; credentials never reach the Agent."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
from easel.video_workshop import WorkshopError

MODELS = {
    'easel-ppu-h3-local::minimax-h3-dense8': ('H3_BASE_URL', 'http://127.0.0.1:8878', 'MiniMax H3 Dense8', 1, 15),
    'easel-ppu-h3-local::minimax-h3-base': ('H3_BASE_URL', 'http://127.0.0.1:8878', 'MiniMax H3 Base', 1, 15),
    'easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step': ('H3_REF11_BASE_URL', 'http://127.0.0.1:8881', 'MiniMax H3 全参考', 1, 14),
    'easel-ppu-pika-h3-local::pika-h3-flex-6step': ('PIKA_H3_BASE_URL', 'http://127.0.0.1:8882', 'Pika H3 Flex', 5, 15),
}


class NativeVideoClient:
    def __init__(self, *, api_key=None, repository=None, transport=None):
        from easel.direct_api import read_settings
        self.settings = read_settings(Path(repository) / '.env') if repository else dict(os.environ)
        self.key = api_key if api_key is not None else self.settings.get('VIDEO_API_KEY', '')
        self.transport = transport
        self.keys = {identifier: self.key for identifier in MODELS}
        pika = 'easel-ppu-pika-h3-local::pika-h3-flex-6step'
        if self.settings.get('PIKA_H3_API_TOKEN'):
            self.keys[pika] = self.settings['PIKA_H3_API_TOKEN']
        elif repository is not None and api_key is None:
            connection = Path(repository) / '.easel' / 'pika-h3-api16' / 'connection.json'
            if connection.is_file():
                try:
                    def private(path):
                        stat = path.stat()
                        return not path.is_symlink() and stat.st_mode & 0o077 == 0 and stat.st_size <= 16384 and (not hasattr(os, 'getuid') or stat.st_uid == os.getuid())
                    if not private(connection):
                        raise ValueError
                    configured = json.loads(connection.read_text())
                    token_path = Path(configured['token_file'])
                    if not private(token_path):
                        raise ValueError
                    token = token_path.read_text().strip()
                    if not re.fullmatch(r'[A-Za-z0-9_-]{16,8192}', token):
                        raise ValueError
                    self.keys[pika] = token
                    self.settings.setdefault('PIKA_H3_BASE_URL', configured['base_url'])
                except (OSError, ValueError, KeyError, TypeError):
                    raise WorkshopError('专用 Pika 连接文件不可用，请保留原配置检查', 422) from None

    def _credential(self, model_id):
        key = self.keys.get(model_id, '')
        if not key:
            raise WorkshopError('当前视频通道未配置专用凭据', 422)
        return key

    def _base(self, model_id):
        if model_id not in MODELS:
            raise WorkshopError('视频模型尚未配置', 422)
        variable, fallback, *_ = MODELS[model_id]
        base = self.settings.get(variable) or fallback
        parsed = urlsplit(base)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.strip('/')):
            raise WorkshopError('PPU 服务须使用运营方配置的本机 HTTP 地址', 422)
        self._credential(model_id)
        return base.rstrip('/')

    def catalog(self):
        models = []
        for identifier, (_, _, name, minimum, maximum) in MODELS.items():
            if not self.keys.get(identifier):
                continue
            self._base(identifier)
            reference = 'ref11' in identifier
            pika = 'pika' in identifier
            models.append({'modelId': identifier, 'name': name, 'textToVideo': not reference,
                'imageToVideo': True, 'referenceToVideo': reference, 'ratios': ['9:16', '16:9'],
                'resolutions': ['480p'] if pika else ['480p', '720p', '1080p', '1440p', '2160p'],
                'defaultResolution': '480p' if pika else '720p',
                'duration': {'min': minimum, 'max': maximum, 'step': 1, 'default': max(6, minimum)},
                'cost': 'unknown', 'readiness': 'not_checked'})
        return models

    def _request(self, method, model_id, path, body=None):
        try:
            with httpx.Client(base_url=self._base(model_id), headers={'Authorization': 'Bearer ' + self._credential(model_id)},
                    trust_env=False, follow_redirects=False, timeout=120, transport=self.transport) as client:
                response = client.request(method, path, json=body)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise WorkshopError('视频服务请求未取得可核对回执，请查询原任务', 503) from None

    def submit(self, spec, operation_id, outputs):
        from easel.video_generation_references import verify_references
        refs = spec.get('references', [])
        paths = verify_references(outputs, refs)
        body = {'model': spec['modelId'].split('::')[-1], 'prompt': spec['prompt'], 'seconds': spec['seconds'],
                'size': spec['aspectRatio'], 'request_id': operation_id}
        for reference, path in zip(refs, paths):
            value = 'data:' + reference['mimeType'] + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            if 'ref11' in spec['modelId'] or 'pika' in spec['modelId']:
                body.setdefault(reference['kind'] + 's', []).append({'data_url': value})
            else:
                body['image'] = value
        return self._request('POST', spec['modelId'], '/v1/videos', body)

    @staticmethod
    def _task(task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'video_[a-f0-9]{32}', task_id):
            raise WorkshopError('原视频任务编号无效', 409)
        return task_id

    def status(self, model_id, task_id):
        return self._request('GET', model_id, '/v1/videos/' + self._task(task_id))

    def download(self, model_id, task_id, destination):
        destination = Path(destination)
        partial = destination.with_suffix('.partial')
        try:
            with httpx.Client(base_url=self._base(model_id), headers={'Authorization': 'Bearer ' + self._credential(model_id)},
                    trust_env=False, follow_redirects=False, timeout=180, transport=self.transport) as client:
                with client.stream('GET', '/v1/videos/' + self._task(task_id) + '/content') as response:
                    response.raise_for_status()
                    count = 0
                    with partial.open('xb') as output:
                        for chunk in response.iter_bytes():
                            count += len(chunk)
                            if count > 1024 * 1024 * 1024:
                                raise WorkshopError('生成文件超过本机交付上限', 422)
                            output.write(chunk)
            os.replace(partial, destination)
        except httpx.HTTPError:
            raise WorkshopError('原视频下载尚未完成，请继续收取原任务', 503) from None
        finally:
            partial.unlink(missing_ok=True)
