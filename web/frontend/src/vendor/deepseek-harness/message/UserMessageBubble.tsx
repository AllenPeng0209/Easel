/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Vendored from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: packages/client/ui-primitives/src/UserMessageBubble.tsx
 * Easel changes: local import bridge and React 19 inert compatibility only.
 * License: integrations/deepseek-harness/LICENSE.
 */
/** Shared user-message background, typography, and content inset. */
import type { ComponentPropsWithoutRef } from 'react'
import { cx as clsx } from './classes'
import css from './UserMessageBubble.module.css'

/** Native div attributes for user-authored message content. */
export type UserMessageBubbleProps = ComponentPropsWithoutRef<'div'>

/**
 * Render user-style message content without row placement or actions.
 * @param props - Native div attributes and the caller-owned content.
 * @returns A theme-aware message surface that wraps its content.
 */
export function UserMessageBubble({ className, ...props }: UserMessageBubbleProps) {
  return <div {...props} className={clsx(css.bubble, className)} />
}
