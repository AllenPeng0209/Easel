/**
 * Turn-positioned failure feedback adapted from DeepSeek Harness
 * ui-chat/src/client/chat/MessageItem.tsx @ ce7073d (MIT).
 * 响芽 projects only allowlisted Host classification fields into fixed copy.
 */
import { useRef, useState } from 'react';
import type { ChatFailure } from '../lib/store';
import css from './ChatFailureNotice.module.css';

const failures = {
  authentication: { title: '模型认证未通过', hint: '请在设置中核对引擎登录状态或 API 凭据。' },
  model_unavailable: { title: '所选模型不可用', hint: '请在设置中核对模型和账号的可用范围。' },
  rate_limit: { title: '模型请求受到频率限制', hint: '请检查模型服务的请求频率限制。' },
  server_queue_full: { title: '服务器的 AI 队列已满', hint: '本轮尚未调用模型。请稍后重试，已有任务会继续处理。' },
  server_owner_queue_full: { title: '你的排队任务已达上限', hint: '本轮尚未调用模型。请等待或取消你已有的排队任务后重试。' },
  server_queue_timeout: { title: '等待服务器资源超时', hint: '本轮尚未调用模型。可以稍后重试。' },
  server_unavailable: { title: '服务器暂时无法接收任务', hint: '本轮尚未调用模型。请稍后再试；若持续出现，请联系管理员检查服务器资源设置。' },
  quota_exhausted: { title: '模型服务额度不足', hint: '请检查模型服务的额度设置。' },
  network: { title: '连接未成功', hint: '请检查网络连接或本机引擎服务。' },
  timeout: { title: '等待引擎响应超时', hint: '请检查引擎服务状态。' },
  permission_denied: { title: '当前操作未获授权', hint: '请核对当前 IP 的角色或服务权限。' },
  session_conflict: { title: '当前对话有未结束的任务', hint: '请先核对正在执行的回合。' },
  storage: { title: '本地记录未能保存', hint: '请检查本地存储，再核对已有记录。' },
  unknown: { title: '本轮执行失败', hint: '请核对本轮工具记录和已有结果。' },
};
const phaseLabels = { startup: '启动阶段', execution: '执行阶段', finalization: '结果写入阶段' };
const settingsCodes = new Set(['authentication', 'model_unavailable', 'quota_exhausted']);
const retryCodes = new Set(['rate_limit', 'network', 'timeout', 'server_queue_full', 'server_owner_queue_full', 'server_queue_timeout']);

export default function ChatFailureNotice({ failure, onOpenSettings, onRetry }: {
  failure?: ChatFailure;
  onOpenSettings?: () => void;
  onRetry?: () => void;
}) {
  const valid = typeof failure?.phase === 'string' && Object.hasOwn(phaseLabels, failure.phase)
    && typeof failure.retryable === 'boolean' && typeof failure.code === 'string' && Object.hasOwn(failures, failure.code);
  const phase = valid ? failure.phase : undefined;
  const code = valid ? failure.code : 'unknown';
  const { title, hint } = failures[code];
  const canOpenSettings = onOpenSettings && settingsCodes.has(code);
  const canRetry = onRetry && failure?.retryable === true && phase === 'startup' && retryCodes.has(code);
  const requested = useRef(false);
  const [retrying, setRetrying] = useState(false);
  return <section className={css.notice} role="status" data-chat-failure={code} data-failure-phase={phase}>
    <div className={css.copy}>
      <strong>{title}</strong>
      {phase && <span className={css.phase}>{phaseLabels[phase]}</span>}
      <p>{hint}</p>
    </div>
    {(canOpenSettings || canRetry) && <div className={css.actions}>
      {canOpenSettings && <button type="button" data-chat-failure-action="settings" onClick={onOpenSettings}>打开设置</button>}
      {canRetry && <button type="button" data-chat-failure-action="retry" disabled={retrying} onClick={() => {
        if (requested.current) return;
        requested.current = true;
        setRetrying(true);
        onRetry();
      }}>{retrying ? '已请求重试' : '重试本轮'}</button>}
    </div>}
  </section>;
}
