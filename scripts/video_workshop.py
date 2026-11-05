#!/usr/bin/env python3
"""Authenticated native Xiangya video production commands."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

class InputError(ValueError):
    pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def open_request(request, timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)

def local_base(value):
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}
            and not url.username and not url.password and not url.query and not url.fragment
            and not url.path.strip('/') and url.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise InputError('base-url must be a local HTTP origin')
    return value.rstrip('/')

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value):
        raise InputError('invalid project identifier')
    return value

def relative_media(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '\\' in value or ':' in value or str(path) != value
            or any(part.startswith(('.', '_')) for part in path.parts)
            or path.suffix.lower() not in {'.mp4', '.mov', '.webm', '.m4v', '.mkv'}):
        raise InputError('path must name an existing video relative to outputs')
    return value

def easel_request(base, project, path, payload=None, method=None):
    session = os.environ.get('EASEL_VIDEO_SESSION', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session):
        raise InputError('请使用 EASEL_VIDEO_SESSION 提供当前登录会话')
    prefix = '/api/workshop' if project == 'default' else '/api/projects/' + identifier(project) + '/workshop'
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(base + prefix + path, data=raw,
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': 'easel_session=' + session},
        method=method or ('GET' if payload is None else 'POST'))
    with open_request(request, timeout=180) as response:
        print(json.dumps(json.loads(response.read()), ensure_ascii=False, indent=2))
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('EASEL_WORKSHOP_BASE_URL', 'http://127.0.0.1:7860'))
    parser.add_argument('--project', default='default')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status'); commands.add_parser('projects')
    create = commands.add_parser('create')
    create.add_argument('--title', required=True); create.add_argument('--brief', default='')
    create.add_argument('--aspect-ratio', choices=['9:16', '16:9', '1:1', '4:5'], default='9:16')
    for name in ['project', 'production', 'register-delivery', 'comment', 'review']:
        sub = commands.add_parser(name); sub.add_argument('project_id')
        if name == 'project':
            continue
        sub.add_argument('--expected-revision', type=int, required=True)
        if name == 'production':
            sub.add_argument('--params-file', type=Path, required=True)
        elif name == 'register-delivery':
            sub.add_argument('--path', required=True)
        else:
            sub.add_argument('--kind', choices=['human', 'simulated'], required=True); sub.add_argument('--notes', default='')
            if name == 'comment':
                sub.add_argument('--at-seconds', type=float, required=True); sub.add_argument('--shot-id')
            else:
                sub.add_argument('--artifact-id', required=True); sub.add_argument('--decision', choices=['approved', 'changes_requested'], required=True)
    args = parser.parse_args(argv)
    try:
        base, project = local_base(args.base_url), identifier(args.project)
        if args.command in {'status', 'projects'}:
            return easel_request(base, project, '/' + args.command)
        if args.command == 'create':
            return easel_request(base, project, '/projects', {'title': args.title, 'brief': args.brief, 'aspect_ratio': args.aspect_ratio})
        route = '/projects/' + identifier(args.project_id)
        if args.command == 'project':
            return easel_request(base, project, route)
        if args.expected_revision < 1:
            raise InputError('内容版本必须为正整数')
        payload = {'expected_revision': args.expected_revision}
        if args.command == 'production':
            data = json.loads(args.params_file.read_text())
            return easel_request(base, project, route + '/production', {**data, **payload}, method='PUT')
        if args.command == 'register-delivery':
            return easel_request(base, project, route + '/deliverables', {**payload, 'path': relative_media(args.path)})
        payload.update(kind=args.kind, notes=args.notes)
        if args.command == 'comment':
            payload['at_seconds'] = args.at_seconds
            if args.shot_id:
                payload['shot_id'] = identifier(args.shot_id)
            return easel_request(base, project, route + '/comments', payload)
        payload.update(artifact_id=identifier(args.artifact_id), decision=args.decision)
        return easel_request(base, project, route + '/reviews', payload)
    except (ValueError, OSError, urllib.error.URLError) as error:
        print('工坊操作未完成：' + str(error), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
