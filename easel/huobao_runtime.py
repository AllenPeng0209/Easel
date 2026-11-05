"""Private, IP-scoped runtime and data bridge for the unmodified Huobao production flow."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from easel.host_state import generation_state_directory
from easel.video_provider import NativeVideoClient
from easel.video_workshop import WorkshopStore, WorkshopError, _digest, _file_lock, _now

SOURCE = Path(__file__).resolve().parents[1] / 'integrations' / 'huobao-drama' / 'source'
RUNTIME_FILE = SOURCE.parent / '.runtime.json'


def _json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(name, path)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class HuobaoRuntime:
    def __init__(self, repository, workspace, ip, *, directory=None):
        self.repository, self.workspace = Path(repository).resolve(), Path(workspace).resolve()
        self.directory = Path(directory) if directory is not None else generation_state_directory(self.repository, ip).parent / 'huobao-drama'
        self.directory = self.directory.resolve()
        self.ip = ip
        self.store = WorkshopStore(self.workspace / 'outputs')
        self.process = None
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.directory / 'runtime.json'
        self.bindings_path = self.directory / 'bindings.json'
        self.database_path = self.directory / 'data' / 'huobao.sqlite3'
        self.data_root = self.directory / 'data'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.is_file() else {}

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def request(self, method, path, data=None):
        try:
            with httpx.Client(base_url=self.state['base'], headers={'X-Xiangya-Runtime': self.state['token']},
                              trust_env=False, follow_redirects=False, timeout=120) as client:
                result = client.request(method, path, json=data)
                result.raise_for_status()
                return result.json()
        except (httpx.HTTPError, ValueError, KeyError):
            raise WorkshopError('火宝制作服务暂时不可用，请刷新原项目', 503) from None

    @staticmethod
    def _node_runtime():
        """Use the Node that built native dependencies, not the launcher's PATH."""
        try:
            if RUNTIME_FILE.is_file():
                value = json.loads(RUNTIME_FILE.read_text())
                if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                        or not isinstance(value.get('node'), str)
                        or not Path(value['node']).is_absolute()
                        or not isinstance(value.get('version'), str)
                        or not isinstance(value.get('modules'), str)):
                    raise ValueError
                node = value['node']
            else:
                node = shutil.which('node')
                value = {'node': node}
            if not node or not Path(node).is_file() or not os.access(node, os.X_OK):
                raise ValueError
            return value
        except (OSError, ValueError, TypeError):
            raise WorkshopError('火宝制作引擎的 Node 运行时未就绪，请重新运行集成安装脚本', 503) from None

    @staticmethod
    def _verify_node_runtime(runtime):
        # Loading the real addon catches ABI and architecture mismatches before
        # opening any project database or starting an immediately dying server.
        script = ("const Database = require(process.argv[1]);"
                  "const db = new Database(':memory:'); db.close();"
                  "process.stdout.write(JSON.stringify({node:process.execPath,"
                  "version:process.version,modules:process.versions.modules}));")
        try:
            result = subprocess.run([runtime['node'], '-e', script,
                                     str(SOURCE / 'backend' / 'node_modules' / 'better-sqlite3')],
                                    check=True, capture_output=True, text=True, timeout=15)
            actual = json.loads(result.stdout)
            if any(runtime.get(key) and runtime[key] != actual.get(key) for key in ('version', 'modules')):
                raise ValueError
            return actual
        except (OSError, ValueError, subprocess.SubprocessError):
            raise WorkshopError('火宝制作引擎的 Node 与原生依赖不匹配，请重新运行集成安装脚本', 503) from None

    def ensure(self):
        node_runtime = self._node_runtime()
        binaries = {'node_bin': node_runtime['node'],
                    'ffmpeg_bin': shutil.which('ffmpeg') or '', 'ffprobe_bin': shutil.which('ffprobe') or ''}
        if self.state:
            try:
                if self.request('GET', '/api/v1/health').get('status') == 'ok' and all(self.state.get(key)==value for key,value in binaries.items()):
                    return
            except WorkshopError:
                pass
        executable = SOURCE / 'backend' / 'node_modules' / '.bin' / 'tsx'
        if not executable.is_file():
            raise WorkshopError('火宝制作引擎依赖尚未安装，请运行集成安装脚本', 503)
        with _file_lock(self.directory / 'start.lock'):
            self._verify_node_runtime(node_runtime)
            if self.state_path.is_file():
                self.state = json.loads(self.state_path.read_text())
                healthy = False
                try:
                    healthy = self.request('GET', '/api/v1/health').get('status') == 'ok'
                except WorkshopError:
                    pass
                if healthy:
                    if all(self.state.get(key) == value for key, value in binaries.items()):
                        return
                    with self.database() as db:
                        active_tasks = db.execute("SELECT COUNT(*) FROM sys_task WHERE status='processing'").fetchone()[0]
                        active_merges = db.execute("SELECT COUNT(*) FROM video_merges WHERE status='processing'").fetchone()[0]
                        if active_tasks or active_merges:
                            return
                    pid = self.state.get('pid')
                    if not isinstance(pid, int):
                        raise WorkshopError('原制作引擎的进程身份待核对', 503)
                    try:
                        if os.getpgid(pid) != pid:
                            raise WorkshopError('原制作引擎的进程身份待核对', 503)
                        os.killpg(pid, signal.SIGTERM)
                        time.sleep(.2)
                    except ProcessLookupError:
                        pass
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            self.state = {'base': f'http://127.0.0.1:{port}', 'token': secrets.token_urlsafe(32),
                          'instance': self.state.get('instance', secrets.token_hex(12)), **binaries}
            workspace = self.directory / 'workspace'
            if not workspace.exists():
                shutil.copytree(SOURCE / 'backend' / 'workspace', workspace)
            self.data_root.mkdir(exist_ok=True)
            env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG') if key in os.environ}
            env.update(PORT=str(port), SQLITE_PATH=str(self.database_path), HUOBAO_DATA_DIR=str(self.data_root),
                STORAGE_PATH=str(self.data_root / 'static'), WORKSPACE_PATH=str(workspace), MYSQL_AUTO_IMPORT='false',
                XIANGYA_RUNTIME_TOKEN=self.state['token'], XIANGYA_INSTANCE_ID=self.state['instance'],
                FFMPEG_BIN=binaries['ffmpeg_bin'], FFPROBE_BIN=binaries['ffprobe_bin'])
            log = os.open(self.directory / 'backend.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(log, 'ab') as stream:
                self.process = subprocess.Popen([node_runtime['node'], str(executable), 'src/index.ts'], cwd=SOURCE / 'backend', env=env,
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
            self.state['pid'] = self.process.pid
            _json(self.state_path, self.state)
            for _ in range(150):
                if self.process.poll() is not None:
                    raise WorkshopError('火宝制作服务启动失败，请检查私有运行日志', 503)
                try:
                    self.request('GET', '/api/v1/health'); break
                except WorkshopError:
                    time.sleep(.1)
            else:
                raise WorkshopError('火宝制作服务启动尚未确认', 503)
            self.seed_configuration()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)

    def seed_configuration(self):
        from easel.direct_api import read_settings
        settings = read_settings(self.repository / '.env')
        native = NativeVideoClient(repository=self.repository)
        with self.database() as db:
            def config(name, kind, provider, base, key, models, priority=0):
                found = db.execute('SELECT id FROM ai_service_configs WHERE name=?', (name,)).fetchone()
                if found: return found[0]
                return db.execute('INSERT INTO ai_service_configs(service_type,provider,name,base_url,api_key,model,priority,is_default,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (kind, provider, name, base, key, json.dumps(models), priority, bool(priority), 1, _now(), _now())).lastrowid
            config('响芽 · 当前 AI 底座', 'text', 'openai', 'http://127.0.0.1:7860/xiangya-agent', self.state['token'], ['xiangya-agent'], 100)
            image_model = settings.get('IMG_MODEL') or settings.get('OPENAI_IMAGE_MODEL') or settings.get('IMAGE_MODEL')
            image_key = settings.get('IMG_API_KEY') or settings.get('OPENAI_IMAGE_API_KEY') or settings.get('IMAGE_API_KEY') or settings.get('OPENAI_API_KEY')
            if image_model and image_key:
                config('响芽 · 已配置生图服务', 'image', 'openai', settings.get('IMG_BASE_URL') or settings.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1', image_key, [image_model], 100)
            for model in native.catalog():
                identifier = model['modelId']
                if 'ref11' in identifier: continue
                config('响芽 PPU · ' + model['name'], 'video', 'minimax', native._base(identifier) + '/v1', native._credential(identifier),
                       [identifier.split('::')[-1]], 100 if 'pika' in identifier else 50)

    def bindings(self):
        return json.loads(self.bindings_path.read_text()) if self.bindings_path.is_file() else {}

    def _asset_file(self, relative, identity=None):
        if not relative: return None
        source = self.store._content_path(relative)
        suffix = '-' + hashlib.sha256(str(identity).encode()).hexdigest()[:16] if identity else ''
        destination = self.data_root / 'static' / 'xiangya' / (_digest(source) + suffix + source.suffix.lower())
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists(): shutil.copyfile(source, destination)
        return destination.relative_to(self.data_root).as_posix()

    def _native_file(self, relative):
        if not isinstance(relative, str) or not relative.lstrip('/').startswith('static/'):
            return None
        source = self.data_root / relative.lstrip('/')
        if source.resolve() != source or not source.is_file() or self.data_root not in source.parents:
            return None
        directory = self.store.outputs / '火宝素材'
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (_digest(source) + source.suffix.lower())
        if not destination.exists(): shutil.copyfile(source, destination)
        return destination.relative_to(self.store.outputs).as_posix()

    def _snapshot(self, binding):
        with self.database() as db:
            episode = dict(db.execute('SELECT * FROM episodes WHERE id=?', (binding['episode_id'],)).fetchone())
            result = {'episode': {key: episode[key] for key in ('content','script_content','resolution')}, 'assets': {}, 'shots': [], 'tasks': [], 'merges': []}
            for kind, table in [('character','characters'), ('scene','scenes'), ('prop','props')]:
                result['assets'][kind] = [dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE drama_id=? AND deleted_at IS NULL ORDER BY id', (binding['drama_id'],))]
            result['shots'] = [dict(row) for row in db.execute('SELECT * FROM storyboards WHERE episode_id=? ORDER BY storyboard_number,id', (binding['episode_id'],))]
            for shot in result['shots']:
                shot['character_ids'] = [row[0] for row in db.execute('SELECT character_id FROM storyboard_characters WHERE storyboard_id=? ORDER BY character_id', (shot['id'],))]
                shot['prop_ids'] = [row[0] for row in db.execute('SELECT prop_id FROM storyboard_props WHERE storyboard_id=? ORDER BY prop_id', (shot['id'],))]
            result['tasks'] = [dict(row) for row in db.execute("SELECT id,storyboard_id,drama_id,prompt,task_id,local_path,status,created_at,updated_at FROM sys_task WHERE (drama_id=? OR storyboard_id IN (SELECT id FROM storyboards WHERE episode_id=?)) AND type='video' AND status='completed' ORDER BY id", (binding['drama_id'],binding['episode_id']))]
            result['merges'] = [dict(row) for row in db.execute("SELECT * FROM video_merges WHERE episode_id=? AND status='completed' AND deleted_at IS NULL ORDER BY id", (binding['episode_id'],))]
            return result

    def open(self, identifier):
        self.ensure()
        with _file_lock(self.directory / 'sync.lock'):
            bindings = self.bindings()
            project = self.store.get(identifier)
            binding = bindings.get(identifier)
            if binding is None:
                with self.database() as db:
                    drama = db.execute('INSERT INTO dramas(title,description,style,aspect_ratio,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                        (project['title'], project['brief'], 'realistic', project['aspect_ratio'], 'draft', _now(), _now())).lastrowid
                    image = db.execute("SELECT id FROM ai_service_configs WHERE service_type='image' ORDER BY priority DESC LIMIT 1").fetchone()
                    video = db.execute("SELECT id,model FROM ai_service_configs WHERE service_type='video' ORDER BY priority DESC LIMIT 1").fetchone()
                    resolution = '480p' if video and 'pika' in video['model'] else '720p'
                    episode = db.execute('INSERT INTO episodes(drama_id,episode_number,title,content,script_content,status,resolution,image_config_id,video_config_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (drama, 1, project['title'], project.get('raw_content') or project['brief'], project['script'], 'draft', resolution,
                         image[0] if image else None, video[0] if video else None, _now(), _now())).lastrowid
                binding = {'drama_id': drama, 'episode_id': episode, 'asset_map': {}, 'shot_map': {}, 'candidate_map': {},
                           'merge_map': {}, 'native_revision': 0, 'engine_digest': None, 'collected_merges': []}
                bindings[identifier] = binding
                _json(self.bindings_path, bindings)
            current_digest = _fingerprint(self._snapshot(binding))
            if binding['engine_digest'] and current_digest != binding['engine_digest']:
                if project['revision'] != binding['native_revision']:
                    raise WorkshopError('对话制作与火宝工程同时有新修改，请先保留双方版本后核对', 409)
                project = self._pull(identifier, binding)
            self._canonical_selection(project,binding)
            if project['revision'] != binding['native_revision']:
                self._push(project, binding)
            # Record only the revision actually imported. A newer native edit
            # can arrive while artwork is extracted and must be pushed next time.
            binding['native_revision'] = project['revision']
            binding['engine_digest'] = _fingerprint(self._snapshot(binding))
            _json(self.bindings_path, bindings)
            return binding

    def _canonical_selection(self, project, binding):
        with self.database() as db:
            for shot in project['shots']:
                candidate=next((row for row in shot['candidates'] if row['id']==shot['selected_candidate_id']),None)
                task_id=binding['candidate_map'].get(shot['selected_candidate_id'])
                if not candidate or not task_id or shot['id'] not in binding['shot_map']:continue
                row=db.execute('SELECT local_path FROM sys_task WHERE id=?',(task_id,)).fetchone()
                current=db.execute('SELECT video_url FROM storyboards WHERE id=?',(binding['shot_map'][shot['id']],)).fetchone()
                if not row or not current or not current[0] or not current[0].startswith('static/xiangya/') or row[0]==current[0]:continue
                try:
                    before=self.data_root/current[0];after=self.data_root/row[0]
                    if before.is_file() and after.is_file() and _digest(before)==_digest(after)==candidate['sha256']:
                        db.execute('UPDATE storyboards SET video_url=? WHERE id=?',(row[0],binding['shot_map'][shot['id']]))
                except (OSError,TypeError):pass

    def _push(self, project, binding):
        artwork=[]
        with self.database() as db:
            db.execute('UPDATE dramas SET title=?,description=?,aspect_ratio=?,updated_at=? WHERE id=?',
                (project['title'], project['brief'], project['aspect_ratio'], _now(), binding['drama_id']))
            db.execute('UPDATE episodes SET content=?,script_content=?,updated_at=? WHERE id=?',
                (project.get('raw_content') or project['brief'], project['script'], _now(), binding['episode_id']))
            active = set()
            for asset in project['assets']:
                kind = asset['kind'] if asset['kind'] in {'character','scene','prop'} else 'character'
                if asset['kind'] not in {'character','scene','prop','image'}: continue
                table, link, field = {'character':('characters','episode_characters','character_id'),
                    'scene':('scenes','episode_scenes','scene_id'), 'prop':('props','episode_props','prop_id')}[kind]
                mapped = binding['asset_map'].get(asset['id'])
                media = self._asset_file(asset.get('path'))
                if media:artwork.append(media)
                if not mapped:
                    if kind == 'scene':
                        row_id = db.execute('INSERT INTO scenes(drama_id,episode_id,location,time,prompt,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                            (binding['drama_id'], binding['episode_id'], asset['name'], '', asset.get('description',''), _now(), _now())).lastrowid
                    else:
                        row_id = db.execute(f'INSERT INTO {table}(drama_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)',
                            (binding['drama_id'], asset['name'], asset.get('description',''), _now(), _now())).lastrowid
                    mapped = {'kind':kind,'id':row_id};binding['asset_map'][asset['id']] = mapped
                name_field = 'location' if kind == 'scene' else 'name'
                description_field = 'prompt' if kind == 'scene' else 'description'
                db.execute(f'UPDATE {table} SET {name_field}=?,{description_field}=?,image_url=?,final_prompt=?,updated_at=?,deleted_at=NULL WHERE id=?',
                    (asset['name'],asset.get('description',''),media,asset.get('image_prompt',''),_now(),mapped['id']))
                if not db.execute(f'SELECT 1 FROM {link} WHERE episode_id=? AND {field}=?', (binding['episode_id'],mapped['id'])).fetchone():
                    db.execute(f'INSERT INTO {link}(episode_id,{field},created_at) VALUES(?,?,?)',(binding['episode_id'],mapped['id'],_now()))
                if kind == 'character':
                    for field in ('appearance','styling'):
                        if field in asset:db.execute(f'UPDATE characters SET {field}=? WHERE id=?',(asset[field],mapped['id']))
                if kind == 'scene' and 'lighting' in asset:db.execute('UPDATE scenes SET lighting=? WHERE id=?',(asset['lighting'],mapped['id']))
                active.add(asset['id'])
            for native, mapped in binding['asset_map'].items():
                if native not in active:
                    table={'character':'characters','scene':'scenes','prop':'props'}[mapped['kind']]
                    db.execute(f'UPDATE {table} SET deleted_at=? WHERE id=?',(_now(),mapped['id']))
            active_shots = set()
            for index, shot in enumerate(project['shots'],1):
                shot_id = binding['shot_map'].get(shot['id'])
                if not shot_id:
                    shot_id = db.execute('INSERT INTO storyboards(episode_id,storyboard_number,created_at,updated_at) VALUES(?,?,?,?)',
                        (binding['episode_id'],index,_now(),_now())).lastrowid
                    binding['shot_map'][shot['id']] = shot_id
                references = [binding['asset_map'][value] for value in shot['asset_ids'] if value in binding['asset_map']]
                scene = next((row['id'] for row in references if row['kind']=='scene'),None)
                for table,column,kind in [('storyboard_characters','character_id','character'),('storyboard_props','prop_id','prop')]:
                    db.execute(f'DELETE FROM {table} WHERE storyboard_id=?',(shot_id,))
                    for ref in references:
                        if ref['kind']==kind:db.execute(f'INSERT INTO {table}(storyboard_id,{column}) VALUES(?,?)',(shot_id,ref['id']))
                selected = None
                for candidate in shot['candidates']:
                    existing = db.execute('SELECT local_path FROM sys_task WHERE id=?', (binding['candidate_map'][candidate['id']],)).fetchone() if candidate['id'] in binding['candidate_map'] else None
                    media = existing['local_path'] if existing and existing['local_path'] else self._asset_file(candidate['path'], candidate['id'])
                    if media and media.startswith('static/xiangya/'):artwork.append(media)
                    if candidate['id'] not in binding['candidate_map']:
                        task = db.execute("INSERT INTO sys_task(type,storyboard_id,drama_id,provider,prompt,local_path,status,created_at,updated_at,completed_at) VALUES('video',?,?,?,?,?,'completed',?,?,?)",
                            (shot_id,binding['drama_id'],'xiangya-import',candidate.get('prompt',shot['prompt']),media,_now(),_now(),_now())).lastrowid
                        binding['candidate_map'][candidate['id']] = task
                    if candidate['id']==shot['selected_candidate_id']:selected=media
                db.execute('UPDATE storyboards SET storyboard_number=?,title=?,description=?,atmosphere=?,video_prompt=?,duration=?,scene_id=?,video_url=?,updated_at=? WHERE id=?',
                    (index,shot['title'],shot.get('description',''),shot.get('atmosphere',''),shot['prompt'],shot['seconds'],scene,selected,_now(),shot_id))
                active_shots.add(shot['id'])
            for native, shot_id in binding['shot_map'].items():
                if native not in active_shots:db.execute('DELETE FROM storyboards WHERE id=?',(shot_id,))
            for artifact in project['deliverables']:
                if artifact['id'] in binding['merge_map']:continue
                media=self._asset_file(artifact['path'], artifact['id'])
                if media:artwork.append(media)
                merge=db.execute("INSERT INTO video_merges(episode_id,drama_id,title,provider,model,status,merged_url,created_at,completed_at) VALUES(?,?,?,'xiangya-import','imported-master','completed',?,?,?)",
                    (binding['episode_id'],binding['drama_id'],project['title'],media,_now(),_now())).lastrowid
                binding['merge_map'][artifact['id']]=merge
                binding['collected_merges'].append(merge)
        if artwork:
            self.request('POST','/api/v1/xiangya/artwork',{'paths':list(dict.fromkeys(artwork))})

    def pull(self, identifier):
        if not self.bindings_path.is_file():return self.store.get(identifier)
        with _file_lock(self.directory / 'sync.lock'):
            bindings=self.bindings();binding=bindings.get(identifier)
            if binding is None:return self.store.get(identifier)
            # A failed first import has no engine baseline to reconcile. The
            # native project remains authoritative until open() retries it.
            if not binding.get('engine_digest'):return self.store.get(identifier)
            digest=_fingerprint(self._snapshot(binding))
            if digest==binding['engine_digest']:return self.store.get(identifier)
            if self.store.get(identifier)['revision']!=binding['native_revision']:
                raise WorkshopError('对话与制作页同时有新修改，未覆盖任何一方',409)
            project=self._pull(identifier,binding)
            binding.update(native_revision=project['revision'],engine_digest=_fingerprint(self._snapshot(binding)))
            _json(self.bindings_path,bindings)
            return project

    def _pull(self, identifier, binding):
        snapshot=self._snapshot(binding);project=self.store.get(identifier)
        old_assets={row['id']:row for row in project['assets']}
        old_shots={row['id']:row for row in project['shots']}
        reverse_assets={(value['kind'],value['id']):key for key,value in binding['asset_map'].items()}
        pulled_assets={}
        for kind,rows in snapshot['assets'].items():
            for row in rows:
                native=reverse_assets.get((kind,row['id']),f"huobao-{kind}-{row['id']}")
                binding['asset_map'][native]={'kind':kind,'id':row['id']}
                previous=old_assets.get(native)
                asset=dict(previous) if previous else {'id':native,'kind':kind}
                media=self._native_file(row.get('image_url')) or ''
                if media and previous and previous.get('sha256')==_digest(self.store._content_path(media)):
                    media=previous.get('path',media)
                fields={'name':row.get('name') or row.get('location') or '素材',
                    'description':row.get('prompt' if kind == 'scene' else 'description') or '',
                    'image_prompt':row.get('final_prompt') or '', 'path':media,
                    **{key:row[key] or '' for key in ('appearance','styling','lighting') if key in row}}
                for key,value in fields.items():
                    # SQL NULL and an absent optional native field both display
                    # as empty. A readback must not invent a content edit.
                    if previous is None or (previous.get(key) or '')!=value:
                        asset[key]=value
                if not media:
                    # Clearing a reference removes the current file identity;
                    # the immutable versions list still retains its history.
                    asset.pop('sha256',None)
                    asset.pop('created_at',None)
                pulled_assets[native]=asset
        represented={row['id'] for row in project['assets']
                     if row['kind'] in {'character','scene','prop','image'} and row['id'] in binding['asset_map']}
        # Keep native order, types and fields that the upstream schema cannot
        # represent. Only an actual upstream deletion removes a mapped asset.
        assets=[pulled_assets.pop(row['id']) if row['id'] in pulled_assets else row
                for row in project['assets'] if row['id'] in pulled_assets or row['id'] not in represented]
        assets.extend(pulled_assets.values())
        available={row['id'] for row in assets}
        reverse_shots={value:key for key,value in binding['shot_map'].items()}
        shots=[]
        for row in snapshot['shots']:
            native=reverse_shots.get(row['id'],f"huobao-shot-{row['id']}");binding['shot_map'][native]=row['id']
            upstream_refs=[key for key,value in binding['asset_map'].items() if key in available and ((value['kind']=='character' and value['id'] in row['character_ids'])
                  or (value['kind']=='scene' and value['id']==row['scene_id']) or (value['kind']=='prop' and value['id'] in row['prop_ids']))]
            previous=old_shots.get(native,{})
            refs=[key for key in previous.get('asset_ids',[]) if key in available
                  and (key not in represented or key in upstream_refs)]
            refs.extend(key for key in upstream_refs if key not in refs)
            shots.append({**previous,'id':native,'title':row.get('title') or f"分镜 {row['storyboard_number']:02d}",
                'description':row.get('description') or '', 'atmosphere':row.get('atmosphere') or '',
                'prompt':row.get('video_prompt') or '', 'seconds':max(1,min(60,row.get('duration') or 5)), 'asset_ids':refs})
        content=snapshot['episode']['content'] or ''
        raw_content=project['raw_content'] if content==(project.get('raw_content') or project['brief']) else content
        script=snapshot['episode']['script_content'] or ''
        if (raw_content,script,assets,shots)!=(project['raw_content'],project['script'],project['assets'],project['shots']):
            project=self.store.production(identifier,project['revision'],{'raw_content':raw_content,
                'script':script, 'assets':assets,'shots':shots})
        reverse_candidates={value:key for key,value in binding['candidate_map'].items()}
        candidates_by_path={}
        for task in snapshot['tasks']:
            native_shot=next((key for key,value in binding['shot_map'].items() if value==task['storyboard_id']),None)
            if native_shot is None or not any(row['id']==native_shot for row in project['shots']):continue
            path=self._native_file(task.get('local_path'))
            if not path:continue
            candidate=reverse_candidates.get(task['id']) or hashlib.sha256(f"{self.state['instance']}:{task['id']}".encode()).hexdigest()[:32]
            binding['candidate_map'][candidate]=task['id']
            project=self.store.add_candidate(identifier,project['revision'],native_shot,path,candidate_id=candidate,
                task_id=task.get('task_id'),source_prompt=task.get('prompt') or '',auto_select=False)
            candidates_by_path[(native_shot,task.get('local_path'))]=candidate
        for row in snapshot['shots']:
            native_shot=next(key for key,value in binding['shot_map'].items() if value==row['id'])
            candidate=candidates_by_path.get((native_shot,row.get('video_url')))
            if candidate:
                project=self.store.select_candidate(identifier,project['revision'],native_shot,candidate)
        for merge in snapshot['merges']:
            if merge['id'] in binding['collected_merges']:continue
            path=self._native_file(merge.get('merged_url'))
            if path:
                project=self.store.deliver(identifier,project['revision'],path)
                binding['merge_map'][project['deliverables'][-1]['id']]=merge['id']
                binding['collected_merges'].append(merge['id'])
        return project
