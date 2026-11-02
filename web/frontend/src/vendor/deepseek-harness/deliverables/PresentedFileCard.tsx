/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-deliverables/src/client/PresentedFileCard.tsx
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: local dependency/type bridges; all file access remains on
 * Easel authenticated artifact routes. See integrations/deepseek-harness/LICENSE.
 */
import type { ContextMenuTriggerProps, DeliverablesLocale as PropsLocale } from './types'
/** File identity, Sidebar preview, and contributed native actions for one delivery. */
import type { ReactNode } from 'react'
import { resolveWorkspacePath } from './file-metadata'
import { FileTypeIcon } from './icons'
import { fileExtension } from './file-metadata'
import type { PresentedHost } from './presented'
import type { PresentedOpenPhase } from './types'
import { basename, type PresentedPath } from './turn-deliverables'
import css from './Deliverables.module.css'

function cardDescription(description: string | undefined, fallback: string): string {
  const trimmed = description?.replace(/\s*(?:\([^()]*\)|（[^（）]*）)\s*$/u, '').trim()
  return trimmed === undefined || trimmed === '' ? fallback : trimmed
}

/**
 * Render independent file actions without nesting buttons inside a clickable card.
 * @param props - durable file metadata, Sidebar preview, Host capabilities, gesture status, and localized copy.
 * @returns the file card and its anchored action menu.
 */
export function PresentedFileCard({ file, cwd, phase, host, onPreview, contextMenuProps, actions, disabled = false, t }: {
  file: PresentedPath
  cwd: string | undefined
  phase: PresentedOpenPhase | undefined
  host: PresentedHost | null
  onPreview: () => void
  contextMenuProps: ContextMenuTriggerProps
  actions: ReactNode
  /** Easel's persisted declaration can outlive the current file. */
  disabled?: boolean
} & PropsLocale) {
  const reveal = host?.fileManager ?? 'directory'
  const name = basename(file.path)
  const metadata = fileExtension(name).toUpperCase() || t('presented.file')
  const status = phase === undefined
    ? cardDescription(file.description, metadata)
    : t(reveal === 'directory' && phase === 'revealing' ? 'presented.directoryOpening'
      : reveal === 'directory' && phase === 'revealError' ? 'presented.directoryError' : `presented.${phase}`)
  return <div className={css.file} data-presented-file>
    <button type="button" disabled={disabled} className={css.cardPreview} title={resolveWorkspacePath(cwd, file.path)}
      aria-label={t('presented.previewCard', { name: file.path })} onClick={onPreview}
      {...contextMenuProps} />
    <span className={css.fileIcon}><FileTypeIcon path={file.path} size={20} /></span>
    <div className={css.fileBody}>
      <div className={css.details}>
        <span className={css.fileName}>{name}</span>
        <span className={css.description} data-presented-description role={phase === undefined ? undefined : 'status'}
          data-error={phase === 'error' || phase === 'revealError' || phase === 'nativeUnavailable' ? true : undefined}>
          <span className={css.secondaryText}>
            {status}
          </span>
          <span className={css.previewHint}>{t('presented.preview')}</span>
        </span>
      </div>
      <div className={css.actions}>{actions}</div>
    </div>
  </div>
}
