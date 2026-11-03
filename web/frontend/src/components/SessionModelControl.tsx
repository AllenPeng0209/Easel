/**
 * Composer model seat adapted from DeepSeek Harness
 * ui-model-selection/src/client/ModelSelect.tsx @ ce7073d (MIT).
 * Easel uses a native select for its Host-authorized model catalog.
 */
import type { SessionModelCapability } from '../lib/api';

const runtimeNames = new Map([
  ['codex', 'Codex'], ['openclaw', 'OpenClaw'], ['api', 'API 直连'],
  ['claude', 'Claude'], ['gemini', 'Gemini'], ['opencode', 'OpenCode'], ['acp', 'ACP'],
]);

interface Props {
  capability?: SessionModelCapability;
  nextModel?: string | null;
  turnModel?: string;
  running?: boolean;
  disabled?: boolean;
  loading?: boolean;
  onChangeModel?: (model: string | null) => void;
  onReload?: () => void;
}

const unavailableValue = '__unavailable_model_choice__';

export default function SessionModelControl({ capability, nextModel, turnModel, running,
  disabled, loading, onChangeModel, onReload }: Props) {
  if (!capability) {
    return <div className="composer-model-control" data-session-model-control>
      <span role="status">{loading ? '正在读取会话模型…' : '会话模型信息暂不可用。'}</span>
      {!loading && onReload && <button type="button" className="composer-attach-btn" onClick={onReload}>重试</button>}
    </div>;
  }
  const { runtime, model, options, canSelect, reason } = capability;
  const labelOf = (id: string) => options.find(option => option.id === id)?.label || id;
  const runtimeLabel = runtimeNames.get(runtime) || runtime || '底座未确认';
  const observed = turnModel || model;
  const observedLabel = turnModel ? running ? '本轮请求模型' : '最近请求模型' : '会话模型';
  const unavailable = !!nextModel && !options.some(option => option.id === nextModel);
  const canChoose = canSelect && options.length > 0 && onChangeModel;
  const modelDetails = observed ? `${observedLabel} ${labelOf(observed)}` : '模型未确认';
  const availability = reason || (!canChoose ? loading ? '正在读取可选模型…' : '当前会话无法切换模型。' : '');
  const details = [runtimeLabel, modelDetails, availability].filter(Boolean).join(' · ');
  return <div className="composer-model-control" data-session-model-control title={details}>
    {canChoose ? <select className="composer-model-choice" aria-label="下一轮模型" data-next-session-model
        value={unavailable ? unavailableValue : nextModel ?? ''}
        disabled={disabled || loading}
        onChange={event => {
          const value = event.currentTarget.value;
          if (!value || options.some(option => option.id === value)) onChangeModel(value || null);
        }}>
        {unavailable && <option value={unavailableValue} disabled>之前选择的模型不可用</option>}
        <option value="">{model ? `${labelOf(model)} · 默认` : `${runtimeLabel} 默认模型`}</option>
        {options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
    </select> : <span className="composer-model-current">{observed ? labelOf(observed) : runtimeLabel}</span>}
    {!canChoose && nextModel && onChangeModel && <button type="button" className="composer-attach-btn"
      data-reset-session-model disabled={disabled || loading} onClick={() => onChangeModel(null)}>清除原选择</button>}
  </div>;
}
