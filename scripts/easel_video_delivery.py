#!/usr/bin/env python3
"""Turn mastered video episodes into scoped Easel topics and BeefTV deliveries."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

import httpx

from video_workshop import identifier, local_base

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easel.beeftv_bridge import configured_client
from easel.workshop_delivery import load_episodes, produce_episode

LIVE_ORIGIN = "http://127.0.0.1:7860"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project", required=True, help="当前已授权 Easel IP 编号")
    parser.add_argument("--base-url", default=LIVE_ORIGIN)
    parser.add_argument("--episode", action="append", default=[], help="仅交付指定集数，可重复")
    parser.add_argument("--plan-only", action="store_true", help="先登记选题与工坊简报，无须已有成片")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    try:
        ip = identifier(args.project)
        base = local_base(args.base_url)
        workspace = ROOT if ip == "default" else ROOT / "projects" / ip
        if workspace.resolve() != workspace:
            raise ValueError("工作区不得通过符号链接定位")
        outputs = (workspace / "outputs").resolve()
        if outputs != workspace / "outputs":
            raise ValueError("outputs 不得通过符号链接定位")
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 1800:
            raise ValueError("每集渲染等待时间须在 0 到 1800 秒之间")
        episodes = load_episodes(args.manifest, outputs, set(args.episode), args.plan_only)
        token = os.environ.get("EASEL_VIDEO_SESSION", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise ValueError("请通过 EASEL_VIDEO_SESSION 提供当前账号的本机 Easel 会话")
        with httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=120,
                          headers={"Origin": base}, cookies={"easel_session": token}) as client:
            def api(method, path, payload=None):
                response = client.request(method, f"/api/projects/{quote(ip, safe='')}" + path, json=payload)
                if not response.is_success:
                    raise RuntimeError(f"Easel {response.status_code} {method} {path}：{response.json().get('detail', '')}")
                return response.json()
            beeftv = None if args.plan_only else configured_client()
            for episode in episodes:
                print(json.dumps(produce_episode(api, beeftv, base, ip, episode, args.timeout, args.plan_only),
                                 ensure_ascii=False), flush=True)
    except (ValueError, OSError, KeyError, StopIteration, RuntimeError, subprocess.SubprocessError, httpx.HTTPError) as error:
        print(f"成片交付未完成：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
