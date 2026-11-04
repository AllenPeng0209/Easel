/** Minimal React/locale boundary replacing Cordis slot registration. */
import type { HTMLAttributes } from 'react';
export type ContextMenuTriggerProps = Pick<HTMLAttributes<HTMLElement>, 'onContextMenu' | 'onKeyDown' | 'tabIndex'>;
export type PresentedOpenPhase = 'opening' | 'error' | 'revealing' | 'revealError' | 'nativeUnavailable';
export interface DeliverablesLocale { t: (key: string, params?: Record<string, string | number>) => string }
interface LiveBlock { argsRaw: string }
interface ResultBlock {
  kind: 'result'; call?: { argsRaw: string }; isError?: boolean;
  error?: { name: string; code: string }; content: { type: string; text?: string }[];
}
export type ToolCallViewProps = { phase: 'preparing' } | { phase: 'started'; block: LiveBlock | ResultBlock; inspect?: () => void };
