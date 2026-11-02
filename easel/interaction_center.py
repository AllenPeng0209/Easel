"""Durable local inbox, reviewed replies and explicitly labelled simulation receipts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

DEFAULT_CLIENT = '00000000000000000000000000000001'
BLOCKED = ['投诉', '退款', '纠纷', '隐私', '身份证', '银行卡', '自杀', '自伤', '诊断', '疗效']


class InteractionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class InteractionStore:
    def __init__(self, outputs: Path, *, initialize_default_client: bool = True):
        self.directory = Path(outputs).resolve() / '_interactions'
        if self.directory.is_symlink():
            raise InteractionError('互动中心目录不能是符号链接')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'center.sqlite3'
        if self.path.is_symlink():
            raise InteractionError('互动中心数据库不能是符号链接')
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS objects (kind TEXT NOT NULL, id TEXT NOT NULL, client_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind,id), FOREIGN KEY(client_id) REFERENCES clients(id))')
            db.execute('CREATE INDEX IF NOT EXISTS objects_scope ON objects(client_id,kind)')
            db.execute('CREATE TABLE IF NOT EXISTS inbound (channel_id TEXT NOT NULL, external_id TEXT NOT NULL, conversation_id TEXT NOT NULL, PRIMARY KEY(channel_id,external_id))')
            if initialize_default_client and not db.execute('SELECT 1 FROM clients WHERE id=?', (DEFAULT_CLIENT,)).fetchone():
                data = {'id': DEFAULT_CLIENT, 'name': '默认客户', 'created_at': now()}
                db.execute('INSERT INTO clients VALUES (?,?)', (DEFAULT_CLIENT, json.dumps(data, ensure_ascii=False)))
        os.chmod(self.path, 0o600)

    @contextmanager
    def _tx(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _client(db, client_id):
        client_id = client_id or DEFAULT_CLIENT
        if not isinstance(client_id, str) or not re.fullmatch(r'[a-f0-9]{32}', client_id):
            raise InteractionError('客户编号无效')
        if not db.execute('SELECT 1 FROM clients WHERE id=?', (client_id,)).fetchone():
            raise InteractionError('客户工作区不存在', 404)
        return client_id

    @staticmethod
    def _get(db, kind, identity, client_id):
        row = db.execute('SELECT data FROM objects WHERE kind=? AND id=? AND client_id=?', (kind, identity, client_id)).fetchone()
        if row is None:
            raise InteractionError('记录不存在或不属于当前客户', 404)
        return json.loads(row['data'])

    @staticmethod
    def _all(db, kind, client_id):
        return [json.loads(row['data']) for row in db.execute('SELECT data FROM objects WHERE kind=? AND client_id=? ORDER BY rowid', (kind, client_id))]

    @staticmethod
    def _put(db, kind, data, client_id):
        db.execute('INSERT INTO objects(kind,id,client_id,data) VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data',
                   (kind, data['id'], client_id, json.dumps(data, ensure_ascii=False, allow_nan=False)))

    @staticmethod
    def _expect(conversation, revision):
        if type(revision) is not int or conversation['revision'] != revision:
            raise InteractionError('会话已有更新，请重新读取后再处理', 409)

    @staticmethod
    def _event(conversation, kind, message):
        conversation['events'].append({'id': uid(), 'type': kind, 'message': message, 'created_at': now()})

    def _save(self, db, conversation):
        conversation['revision'] += 1
        conversation['updated_at'] = now()
        self._put(db, 'conversation', conversation, conversation['client_id'])
        return conversation

    def _rules(self, db, client_id):
        rules = self._all(db, 'rules', client_id)
        defaults = {'id': client_id, 'client_id': client_id, 'mode': 'assist', 'enabled': False,
                    'blocked_keywords': BLOCKED.copy(), 'max_auto_per_hour': 20, 'max_auto_per_run': 10}
        return {**defaults, **rules[0]} if rules else defaults

    def _auto_count(self, db, client_id, channel_id):
        since = datetime.now(timezone.utc).timestamp() - 3600
        count = 0
        for conversation in self._all(db, 'conversation', client_id):
            if conversation['channel_id'] != channel_id:
                continue
            for send in conversation['sends']:
                if not send.get('approved_by_rule') or send['status'] not in ('sent', 'sending', 'unknown'):
                    continue
                try:
                    recent = datetime.fromisoformat(send['created_at']).timestamp() >= since
                except (ValueError, TypeError):
                    recent = True
                count += int(recent)
        return count

    @staticmethod
    def _capability(channel, capabilities=None):
        if channel['delivery_mode'] == 'simulation':
            return {'state': 'simulation', 'can_sync': False, 'can_send': True, 'message': '模拟通道，不连接真实平台'}
        if capabilities:
            try:
                result = capabilities(dict(channel))
                if isinstance(result, dict):
                    result = {k: result[k] for k in ('state', 'can_sync', 'can_send', 'message') if k in result}
                    if channel['delivery_mode'] == 'manual':
                        result['can_send'] = False
                    return result
            except Exception:
                pass
        if channel['delivery_mode'] == 'manual':
            return {'state': 'manual', 'can_sync': False, 'can_send': False, 'message': '人工处理通道，未启用平台发送'}
        return {'state': 'disconnected', 'can_sync': False, 'can_send': False, 'message': '官方权限或账号配置尚未接通'}

    def bootstrap(self, client_id=None, capabilities=None):
        with self._tx() as db:
            clients = [json.loads(row['data']) for row in db.execute('SELECT data FROM clients ORDER BY rowid')]
            if not clients:
                if client_id:
                    raise InteractionError('客户工作区不存在', 404)
                return {'client_id': '', 'clients': [], 'channels': [], 'conversations': [], 'knowledge': [],
                        'rules': self._rules(db, ''), 'capabilities': [],
                        'counts': {state: 0 for state in ('pending', 'review', 'handled', 'failed')}}
            selected = client_id or (DEFAULT_CLIENT if any(c['id'] == DEFAULT_CLIENT for c in clients) else clients[0]['id'])
            client_id = self._client(db, selected)
            channels = self._all(db, 'channel', client_id)
            conversations = self._all(db, 'conversation', client_id)
            knowledge = self._all(db, 'knowledge', client_id)
            rules = self._rules(db, client_id)
        for channel in channels:
            channel['capabilities'] = self._capability(channel, capabilities)
            channel['status'] = channel['capabilities'].get('state', 'disconnected')
        conversations.sort(key=lambda c: c['updated_at'], reverse=True)
        summaries = [{k: value for k, value in c.items() if k not in ('messages', 'drafts', 'events', 'sends')} for c in conversations]
        return {'client_id': client_id, 'clients': clients, 'channels': channels, 'conversations': summaries,
                'knowledge': knowledge, 'rules': rules, 'capabilities': [c['capabilities'] for c in channels],
                'counts': {state: sum(c['status'] == state for c in conversations) for state in ('pending', 'review', 'handled', 'failed')}}

    def create_client(self, name):
        client_id = uid()
        with self._tx() as db:
            db.execute('INSERT INTO clients VALUES (?,?)', (client_id, json.dumps({'id': client_id, 'name': name, 'created_at': now()}, ensure_ascii=False)))
        return self.bootstrap(client_id)

    def create_channel(self, client_id, values, capabilities=None):
        with self._tx() as db:
            self._client(db, client_id)
            channel = {'id': uid(), 'client_id': client_id, 'name': values['name'], 'platform': values['platform'],
                       'kind': values['kind'], 'delivery_mode': values['delivery_mode'], 'account_label': values.get('account_label', ''),
                       'source_url': values.get('source_url', ''), 'config': values.get('config', {}), 'enabled': values.get('enabled', True), 'created_at': now()}
            if 'credential_binding' in values:
                channel['credential_binding'] = values['credential_binding']
            self._put(db, 'channel', channel, client_id)
        return self.bootstrap(client_id, capabilities)

    def update_channel(self, client_id, channel_id, values, capabilities=None):
        with self._tx() as db:
            self._client(db, client_id)
            channel = self._get(db, 'channel', channel_id, client_id)
            channel.update(values)
            self._put(db, 'channel', channel, client_id)
        return self.bootstrap(client_id, capabilities)

    def conversation(self, client_id, identity):
        with self._tx() as db:
            self._client(db, client_id)
            return self._get(db, 'conversation', identity, client_id)

    def ingest(self, client_id, channel_id, external_id, text, author_name='', conversation_external_id=None, source_url='', subject=''):
        if not isinstance(external_id, str) or not external_id or len(external_id) > 512 or not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise InteractionError('消息编号或正文无效')
        for value, limit in ((author_name, 256), (conversation_external_id or '', 512), (source_url, 4096), (subject, 300)):
            if not isinstance(value, str) or len(value) > limit:
                raise InteractionError('消息来源元数据无效')
        with self._tx() as db:
            self._client(db, client_id)
            channel = self._get(db, 'channel', channel_id, client_id)
            duplicate = db.execute('SELECT conversation_id FROM inbound WHERE channel_id=? AND external_id=?', (channel_id, external_id)).fetchone()
            if duplicate:
                return self._get(db, 'conversation', duplicate['conversation_id'], client_id)
            thread = conversation_external_id or external_id
            conversations = self._all(db, 'conversation', client_id)
            c = next((c for c in conversations if c['channel_id'] == channel_id and c['external_id'] == thread), None)
            if c is None:
                c = {'id': uid(), 'client_id': client_id, 'channel_id': channel_id, 'external_id': thread,
                     'platform': channel['platform'], 'kind': channel['kind'], 'delivery_mode': channel['delivery_mode'],
                     'subject': subject or ('模拟咨询' if channel['delivery_mode'] == 'simulation' else '新咨询'), 'author_name': author_name,
                     'revision': 0, 'status': 'pending', 'human_takeover': False, 'last_error': '', 'messages': [], 'drafts': [], 'events': [], 'sends': [], 'created_at': now()}
            for d in c['drafts']:
                if d['status'] in ('draft', 'approved'):
                    d['status'] = 'superseded'
            c['messages'].append({'id': uid(), 'external_id': external_id, 'direction': 'inbound', 'text': text,
                'author_name': author_name, 'created_at': now(), 'simulated': channel['delivery_mode'] == 'simulation'})
            c.update(status='pending', preview=text[:500], last_message_at=now(), last_inbound_external_id=external_id, source_url=source_url or channel.get('source_url', ''))
            self._event(c, 'message_received', '收到模拟消息' if channel['delivery_mode'] == 'simulation' else '收到平台消息')
            self._save(db, c)
            db.execute('INSERT INTO inbound VALUES(?,?,?)', (channel_id, external_id, c['id']))
            return c

    def add_knowledge(self, client_id, values, identity=None):
        with self._tx() as db:
            self._client(db, client_id)
            data = self._get(db, 'knowledge', identity, client_id) if identity else {'id': uid(), 'client_id': client_id, 'enabled': True, 'auto_send': False, 'created_at': now()}
            if identity and 'auto_send' not in values and any(key in values and values[key] != data.get(key) for key in ('question', 'keywords', 'answer')):
                data['auto_send'] = False
            data.update(values)
            data['updated_at'] = now()
            self._put(db, 'knowledge', data, client_id)
        return self.bootstrap(client_id)

    def update_rules(self, client_id, values):
        with self._tx() as db:
            self._client(db, client_id)
            rules = self._rules(db, client_id)
            rules.update(values)
            # Built-in escalation topics cannot be silently removed by an edit.
            rules['blocked_keywords'] = list(dict.fromkeys([*BLOCKED, *rules.get('blocked_keywords', [])]))
            self._put(db, 'rules', rules, client_id)
        return self.bootstrap(client_id)

    def _draft_container(self, db, client_id, draft_id):
        self._client(db, client_id)
        for c in self._all(db, 'conversation', client_id):
            for d in c['drafts']:
                if d['id'] == draft_id:
                    return c, d
        raise InteractionError('回复草稿不存在或不属于当前客户', 404)

    @staticmethod
    def _last_inbound(c):
        return next((m for m in reversed(c['messages']) if m['direction'] == 'inbound'), None)

    def create_draft(self, client_id, conversation_id, revision):
        with self._tx() as db:
            self._client(db, client_id)
            c = self._get(db, 'conversation', conversation_id, client_id)
            self._expect(c, revision)
            inbound = self._last_inbound(c)
            rules = self._rules(db, client_id)
            text = inbound['text'].casefold()
            blocked = any(word.casefold() in text for word in rules['blocked_keywords'])
            candidates = [] if blocked else [k for k in self._all(db, 'knowledge', client_id) if k['enabled']
                and (c['delivery_mode'] == 'simulation' or not k.get('simulation'))
                and any(word.casefold() in text for word in k['keywords'] if word.strip())]
            selected = candidates[0] if len(candidates) == 1 else None
            for old in c['drafts']:
                if old['status'] in ('draft', 'approved'):
                    old['status'] = 'superseded'
            c['drafts'].append({'id': uid(), 'text': selected['answer'] if selected else '', 'source': 'knowledge_base' if selected else 'manual_required',
                'knowledge_id': selected['id'] if selected else None, 'status': 'draft', 'confidence': 1.0 if selected else 0,
                'basis_message_id': inbound['id'], 'approved_at': None, 'created_at': now(),
                'reason': '命中已启用的知识库条目' if selected else '涉及需人工处理的话题' if blocked else
                          '命中多个知识库条目，需要人工选择' if candidates else '知识库未命中，需要人工或 Agent 编写'})
            c['status'] = 'review'
            self._event(c, 'draft_created', '生成知识库回复建议' if selected else '创建待人工填写的草稿')
            return self._save(db, c)

    def edit_draft(self, client_id, draft_id, revision, text, source='human'):
        with self._tx() as db:
            c, d = self._draft_container(db, client_id, draft_id)
            self._expect(c, revision)
            if d['status'] in ('sent', 'superseded') or any(s['draft_id'] == draft_id and s['status'] in ('sending', 'unknown') for s in c['sends']):
                raise InteractionError('该草稿已发送、失效或结果待核对，请创建新草稿', 409)
            d.update(text=text, source=source, status='draft', knowledge_id=None, confidence=0, approved_at=None)
            c['status'] = 'review'
            self._event(c, 'draft_edited', 'Agent 修改草稿' if source == 'agent' else '编辑回复草稿；原审核已失效')
            return self._save(db, c)

    def approve_draft(self, client_id, draft_id, revision, review_kind='human', *, automatic=False):
        with self._tx() as db:
            c, d = self._draft_container(db, client_id, draft_id)
            self._expect(c, revision)
            channel = self._get(db, 'channel', c['channel_id'], client_id)
            inbound = self._last_inbound(c)
            if d['status'] not in ('draft', 'approved') or d['basis_message_id'] != inbound['id'] or not d['text'].strip():
                raise InteractionError('草稿为空或会话已有新消息，请重新检查', 409)
            if automatic:
                rules = self._rules(db, client_id)
                if not rules['enabled'] or rules['mode'] != 'auto_faq' or c['human_takeover'] or d['source'] != 'knowledge_base':
                    raise InteractionError('未满足已批准 FAQ 自动回复条件', 409)
                knowledge = self._get(db, 'knowledge', d['knowledge_id'], client_id)
                if not knowledge['enabled'] or not knowledge['auto_send'] or knowledge['answer'] != d['text']:
                    raise InteractionError('知识库已改变或未允许自动回复', 409)
                if channel['delivery_mode'] != 'simulation' and knowledge.get('simulation'):
                    raise InteractionError('模拟知识库条目不能自动发送到真实平台', 409)
                kind = 'simulated_review' if channel['delivery_mode'] == 'simulation' else 'rule_review'
            else:
                if channel['delivery_mode'] != 'simulation' and review_kind != 'human':
                    raise InteractionError('真实通道需要人工审核，Agent 不能代签', 409)
                kind = 'simulated_review' if channel['delivery_mode'] == 'simulation' else 'human_review'
            d.update(status='approved', approved_at=now(), approved_sha256=digest(d['text']), review_kind=kind, approved_by_rule=automatic)
            self._event(c, kind, '模拟审核已记录' if kind == 'simulated_review' else '已批准 FAQ 规则审核' if automatic else '人工审核已记录')
            return self._save(db, c)

    def send_draft(self, client_id, draft_id, revision, operation_id, send_adapter=None, capabilities=None):
        if not re.fullmatch(r'[A-Za-z0-9:_-]{1,160}', operation_id):
            raise InteractionError('发送操作编号无效')
        op_id = digest(client_id + ':' + operation_id)
        with self._tx() as db:
            c, d = self._draft_container(db, client_id, draft_id)
            previous = db.execute('SELECT data FROM objects WHERE kind=? AND id=? AND client_id=?', ('send_operation', op_id, client_id)).fetchone()
            if previous:
                record = json.loads(previous['data'])
                if record['draft_id'] != draft_id:
                    raise InteractionError('操作编号已用于其他草稿', 409)
                return c
            self._expect(c, revision)
            if d['status'] == 'sent':
                return c
            if any(s['status'] in ('sending', 'unknown') and
                   (s['draft_id'] == draft_id or s.get('basis_message_id') == d['basis_message_id']) for s in c['sends']):
                raise InteractionError('原发送结果待核对，不能再次发送', 409)
            if d['status'] != 'approved' or d.get('approved_sha256') != digest(d['text']) or d['basis_message_id'] != self._last_inbound(c)['id']:
                raise InteractionError('请先审核当前草稿；新消息或修改会使旧审核失效', 409)
            from skills.shared.scripts.content_guard import BLOCK_CATEGORIES, scan
            if any(finding.category in BLOCK_CATEGORIES for finding in scan(d['text'])):
                raise InteractionError('回复内容包含疑似凭据或内部信息，请修改后重新审核', 409)
            channel = self._get(db, 'channel', c['channel_id'], client_id)
            if d.get('approved_by_rule'):
                rules = self._rules(db, client_id)
                knowledge = self._get(db, 'knowledge', d['knowledge_id'], client_id)
                if (not rules['enabled'] or rules['mode'] != 'auto_faq' or c['human_takeover']
                    or not knowledge['enabled'] or not knowledge['auto_send'] or knowledge['answer'] != d['text']
                    or channel['delivery_mode'] != 'simulation' and knowledge.get('simulation')):
                    raise InteractionError('自动回复条件已有变化，未提交发送', 409)
                if self._auto_count(db, client_id, channel['id']) >= rules['max_auto_per_hour']:
                    raise InteractionError('该通道已达到自动回复小时限额，草稿已保留', 429)
            if not channel['enabled']:
                raise InteractionError('该接待通道已停用', 409)
            capability = self._capability(channel, capabilities)
            if not capability.get('can_send') or (channel['delivery_mode'] != 'simulation' and send_adapter is None):
                raise InteractionError('平台发送尚未接通，草稿已保留', 503)
            record = {'id': op_id, 'operation_id': operation_id, 'draft_id': draft_id, 'basis_message_id': d['basis_message_id'], 'status': 'sending', 'mode': channel['delivery_mode'],
                'approved_by_rule': bool(d.get('approved_by_rule')), 'receipt_id': '', 'external_message_id': '', 'message': '正在提交发送', 'created_at': now()}
            c['sends'].append(record.copy())
            self._put(db, 'send_operation', record, client_id)
            self._event(c, 'send_started', '提交模拟发送' if channel['delivery_mode'] == 'simulation' else '提交平台发送')
            self._save(db, c)
        # Preserve a durable intent before invoking the external transport. Unknown
        # responses never become a new send, even after a process restart.
        try:
            if channel['delivery_mode'] == 'simulation':
                result = {'status': 'sent', 'mode': 'simulation', 'receipt_id': 'sim-' + uid(), 'external_message_id': 'sim-message-' + uid()}
            else:
                result = send_adapter(channel, c, dict(d), operation_id)
            if not isinstance(result, dict) or result.get('status') not in ('sent', 'unknown', 'failed'):
                result = {'status': 'unknown'}
            if result['status'] == 'sent' and (not result.get('external_message_id') or not result.get('receipt_id')):
                result = {'status': 'unknown'}
        except Exception:
            result = {'status': 'unknown'}
        with self._tx() as db:
            c, d = self._draft_container(db, client_id, draft_id)
            saved = next(s for s in c['sends'] if s['operation_id'] == operation_id)
            status = result['status']
            saved.update(status=status, receipt_id=str(result.get('receipt_id', ''))[:512], external_message_id=str(result.get('external_message_id', ''))[:512],
                message='模拟发送完成' if status == 'sent' and channel['delivery_mode'] == 'simulation' else '平台发送已确认' if status == 'sent' else '发送结果待核对，未自动重发' if status == 'unknown' else '平台拒绝了本次发送')
            self._put(db, 'send_operation', saved, client_id)
            if status == 'sent':
                d['status'] = 'sent'
                c['messages'].append({'id': uid(), 'external_id': saved['external_message_id'], 'direction': 'outbound', 'text': d['text'],
                    'author_name': '客服', 'created_at': now(), 'simulated': channel['delivery_mode'] == 'simulation'})
                # Incoming messages arriving during I/O must stay pending.
                c['status'] = 'handled' if self._last_inbound(c)['id'] == d['basis_message_id'] else 'pending'
                c['last_error'] = ''
            else:
                c.update(status='failed', last_error=saved['message'])
                if status == 'unknown':
                    c['human_takeover'] = True
            self._event(c, 'simulation_sent' if status == 'sent' and channel['delivery_mode'] == 'simulation' else 'send_' + status, saved['message'])
            return self._save(db, c)

    def action(self, client_id, identity, revision, action):
        with self._tx() as db:
            self._client(db, client_id)
            c = self._get(db, 'conversation', identity, client_id)
            self._expect(c, revision)
            if action == 'takeover':
                c.update(human_takeover=True, status='review')
            elif action == 'resolve':
                c['status'] = 'handled'
            elif action == 'reopen':
                c.update(status='pending', human_takeover=False)
            else:
                raise InteractionError('未知处理动作')
            self._event(c, action, {'takeover': '已转人工接待，自动回复暂停', 'resolve': '会话已标记处理完成', 'reopen': '会话重新进入待处理队列'}[action])
            return self._save(db, c)

    def simulation(self, client_id):
        with self._tx() as db:
            self._client(db, client_id)
            existing = self._all(db, 'channel', client_id)
            for index, (platform, kind) in enumerate((('xiaohongshu', 'comment'), ('xiaohongshu', 'dm'), ('douyin', 'comment'), ('douyin', 'dm'))):
                identity = digest(client_id + ':simulation-channel:' + str(index))[:32]
                if not any(c['id'] == identity for c in existing):
                    self._put(db, 'channel', {'id': identity, 'client_id': client_id, 'name': ('小红书' if platform == 'xiaohongshu' else '抖音') + ('评论' if kind == 'comment' else '私信') + ' · 模拟',
                        'platform': platform, 'kind': kind, 'delivery_mode': 'simulation', 'account_label': '模拟验收账号', 'enabled': True, 'source_url': '', 'config': {}, 'created_at': now()}, client_id)
            knowledge_id = digest(client_id + ':simulation-faq')[:32]
            if not any(k['id'] == knowledge_id for k in self._all(db, 'knowledge', client_id)):
                self._put(db, 'knowledge', {'id': knowledge_id, 'client_id': client_id, 'question': '想了解课程', 'keywords': ['课程介绍', '了解课程'],
                    'answer': '你好，可以先告诉我你最想改善的亲子沟通问题吗？我先帮你整理咨询需求，具体课程安排由工作人员确认。',
                    'auto_send': True, 'enabled': True, 'simulation': True, 'created_at': now(), 'updated_at': now()}, client_id)
        messages = [('想了解课程介绍，可以讲讲吗？', '课程咨询'), ('我想投诉并申请退款。', '需人工处理'), ('想了解课程，怎么开始沟通？', '咨询接待'), ('能保证孩子一个月提高成绩吗？', '复杂问题')]
        for index, (text, subject) in enumerate(messages):
            self.ingest(client_id, digest(client_id + ':simulation-channel:' + str(index))[:32], 'simulation-message-' + str(index), text,
                        author_name='模拟访客 ' + str(index + 1), subject='[模拟] ' + subject)
        return self.bootstrap(client_id)

    def sync_channel(self, client_id, channel_id, source_url='', sync_adapter=None, capabilities=None):
        with self._tx() as db:
            self._client(db, client_id)
            channel = self._get(db, 'channel', channel_id, client_id)
        if channel['delivery_mode'] == 'simulation':
            return self.bootstrap(client_id, capabilities)
        if not channel['enabled'] or sync_adapter is None or not self._capability(channel, capabilities).get('can_sync'):
            raise InteractionError('通道尚未具备消息读取权限，请先完成接入', 503)
        try:
            items = sync_adapter(channel, source_url=source_url or channel.get('source_url', ''))
            if not isinstance(items, list):
                raise ValueError
            for item in items[:200]:
                if (not isinstance(item, dict) or not isinstance(item.get('external_id'), str)
                    or not item['external_id'] or len(item['external_id']) > 512
                    or not isinstance(item.get('text'), str) or not item['text'].strip() or len(item['text']) > 20000):
                    raise ValueError
                for key, limit in (('author_name', 256), ('conversation_external_id', 512), ('source_url', 4096), ('subject', 300)):
                    value = item.get(key, '')
                    if not isinstance(value, str) or len(value) > limit:
                        raise ValueError
        except Exception:
            raise InteractionError('平台消息读取未完成，请检查账号状态与授权', 503) from None
        for item in items[:200]:
            self.ingest(client_id, channel_id, item['external_id'], item['text'], author_name=item.get('author_name', ''),
                conversation_external_id=item.get('conversation_external_id'), source_url=item.get('source_url', ''), subject=item.get('subject', ''))
        return self.bootstrap(client_id, capabilities)

    def tick(self, client_id=None, send_adapter=None, capabilities=None):
        if client_id is None:
            with self._tx() as db:
                clients = [r['id'] for r in db.execute('SELECT id FROM clients')]
            return {'clients': [self.tick(identity, send_adapter, capabilities) for identity in clients]}
        b = self.bootstrap(client_id, capabilities)
        rules = b['rules']
        if not rules['enabled'] or rules['mode'] != 'auto_faq':
            return b
        channels = {c['id']: c for c in b['channels']}
        attempts = 0
        for summary in b['conversations'][:100]:
            if attempts >= rules['max_auto_per_run']:
                break
            channel = channels[summary['channel_id']]
            if summary['status'] != 'pending' or summary['human_takeover'] or not channel['enabled'] or not channel['capabilities'].get('can_send'):
                continue
            if any(word.casefold() in summary['preview'].casefold() for word in rules['blocked_keywords']):
                continue
            with self._tx() as db:
                if self._auto_count(db, client_id, channel['id']) >= rules['max_auto_per_hour']:
                    continue
            try:
                c = self.create_draft(client_id, summary['id'], summary['revision'])
                d = c['drafts'][-1]
                knowledge = next((k for k in b['knowledge'] if k['id'] == d.get('knowledge_id')), None)
                if not knowledge or not knowledge['auto_send'] or not knowledge['enabled'] or d['source'] != 'knowledge_base':
                    continue
                c = self.approve_draft(client_id, d['id'], c['revision'], automatic=True)
                attempts += 1
                self.send_draft(client_id, d['id'], c['revision'], 'auto-' + digest(channel['id'] + ':' + d['basis_message_id']), send_adapter, capabilities)
            except InteractionError:
                continue
        return self.bootstrap(client_id, capabilities)
