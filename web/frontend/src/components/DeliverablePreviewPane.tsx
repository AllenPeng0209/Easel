import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { ChatDeliverable } from '../lib/deliverables';
import DeliverablePreviewBody from './DeliverablePreviewBody';
import '../styles/deliverablePreviewPane.css';

/**
 * Resize interaction adapted from DeepSeek Harness FileNavigator.tsx, commit
 * ce7073d51254c26cd63b22a60835b8f948deb447.
 * Copyright (c) 2026 DeepSeek. MIT, see integrations/deepseek-harness/LICENSE.
 * Source: packages/client/ui-sidebar-files/src/client/FileNavigator.tsx.
 */
const MOBILE = '(max-width: 767px)';
const MIN_PANE = 320;
const MIN_CHAT = 400;
const MAX_PANE = 960;
const STEP = 20;

export default function DeliverablePreviewPane({ file, onClose }: { file: ChatDeliverable; onClose: () => void }) {
  const pane = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const title = useId();
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  const [geometry, setGeometry] = useState({ mobile: false, maximum: MAX_PANE });
  const [preferredWidth, setPreferredWidth] = useState(480);
  const width = Math.max(MIN_PANE, Math.min(preferredWidth, geometry.maximum));

  useLayoutEffect(() => {
    const container = pane.current?.parentElement;
    if (!container) return;
    const media = window.matchMedia(MOBILE);
    const measure = () => {
      const available = Math.floor(container.getBoundingClientRect().width);
      const mobile = media.matches || (available > 0 && available < MIN_CHAT + MIN_PANE);
      const maximum = available > 0 ? Math.max(MIN_PANE, Math.min(MAX_PANE, available - MIN_CHAT)) : MAX_PANE;
      setGeometry(previous => previous.mobile === mobile && previous.maximum === maximum ? previous : { mobile, maximum });
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(container);
    media.addEventListener('change', measure);
    return () => { observer?.disconnect(); media.removeEventListener('change', measure); };
  }, []);

  useLayoutEffect(() => { close.current?.focus(); }, []);
  useLayoutEffect(() => {
    const aside = pane.current;
    if (!geometry.mobile || !aside) return;
    drag.current = null;
    const chat = aside.parentElement?.querySelector<HTMLElement>(':scope > .chat-page');
    if (!aside.contains(document.activeElement)) close.current?.focus();
    if (!chat || chat.hasAttribute('inert')) return;
    chat.setAttribute('inert', '');
    return () => { chat.removeAttribute('inert'); };
  }, [geometry.mobile]);

  const resize = (next: number) => setPreferredWidth(Math.max(MIN_PANE, Math.min(next, geometry.maximum)));

  return (
    <aside ref={pane} className={`deliverable-preview-pane${geometry.mobile ? ' is-fullscreen' : ''}`}
      role={geometry.mobile ? 'dialog' : undefined} aria-modal={geometry.mobile || undefined} aria-labelledby={title}
      style={{ '--deliverable-preview-width': `${width}px` } as CSSProperties}
      onKeyDown={event => {
        if (event.defaultPrevented) return;
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
        if (!geometry.mobile || event.key !== 'Tab') return;
        const targets = event.currentTarget.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex="0"]');
        const first = targets[0];
        const last = targets[targets.length - 1];
        if (event.shiftKey && event.target === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus(); }
      }}>
      {!geometry.mobile && <div className="deliverable-preview-separator" role="separator" aria-orientation="vertical"
        aria-label="调整交付物预览宽度" aria-valuemin={MIN_PANE} aria-valuemax={geometry.maximum} aria-valuenow={width}
        tabIndex={0}
        onKeyDown={event => {
          const next = event.key === 'ArrowLeft' ? width + STEP : event.key === 'ArrowRight' ? width - STEP
            : event.key === 'Home' ? MIN_PANE : event.key === 'End' ? geometry.maximum : undefined;
          if (next !== undefined) { event.preventDefault(); resize(next); }
        }}
        onPointerDown={event => {
          if (event.button !== 0 || drag.current) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { pointerId: event.pointerId, x: event.clientX, width };
        }}
        onPointerMove={event => {
          const origin = drag.current;
          if (origin?.pointerId === event.pointerId) resize(origin.width + origin.x - event.clientX);
        }}
        onPointerUp={event => {
          if (drag.current?.pointerId !== event.pointerId) return;
          drag.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={event => { if (drag.current?.pointerId === event.pointerId) drag.current = null; }}
        onLostPointerCapture={event => { if (drag.current?.pointerId === event.pointerId) drag.current = null; }} />}
      <header className="deliverable-preview-header">
        <h2 id={title} title={file.name}>{file.name}{file.storageMode === 'snapshot' ? ` · 交付 V${file.revision}` : ''}</h2>
        <button ref={close} type="button" aria-label={geometry.mobile ? '返回对话' : '关闭预览'} onClick={onClose}>
          {geometry.mobile ? '返回对话' : '关闭'}
        </button>
      </header>
      <div className="deliverable-preview-scroll" role="region" tabIndex={0} aria-label="交付物内容">
        <DeliverablePreviewBody key={file.artifactId} file={file} />
      </div>
    </aside>
  );
}
