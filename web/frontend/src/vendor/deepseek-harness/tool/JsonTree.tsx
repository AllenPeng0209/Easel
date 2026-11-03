/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: packages/client/ui-primitives/src/JsonTree.tsx.
 * Easel retains typed previews and recursive rows, using native disclosure and caller-owned copy.
 * License: integrations/deepseek-harness/LICENSE.
 */
import type { ReactNode } from 'react';
import css from './JsonTree.module.css';

const OBJECT_PREVIEW_LIMIT = 4;
const ARRAY_PREVIEW_LIMIT = 5;
const PREVIEW_DEPTH_LIMIT = 2;

function isContainer(value: unknown): value is object {
  return typeof value === 'object' && value !== null;
}

function entriesOf(value: object): [string, unknown][] {
  return Object.entries(value);
}

function bracketOf(value: object): readonly [string, string] {
  return Array.isArray(value) ? ['[', ']'] : ['{', '}'];
}

function previewPrimitive(value: unknown): ReactNode {
  if (typeof value === 'string') return <span className={css.stringValue}>{JSON.stringify(value)}</span>;
  if (typeof value === 'number') return <span className={css.numberValue}>{String(value)}</span>;
  return <span className={css.keywordValue}>{String(value)}</span>;
}

function previewValue(value: unknown, depth = 0): ReactNode {
  if (!isContainer(value)) return previewPrimitive(value);
  const entries = entriesOf(value);
  const array = Array.isArray(value);
  const limit = array ? ARRAY_PREVIEW_LIMIT : OBJECT_PREVIEW_LIMIT;
  const [open, close] = bracketOf(value);

  return <>
    <span className={css.punctuation}>{open}</span>
    {depth >= PREVIEW_DEPTH_LIMIT && entries.length > 0 ? <span className={css.previewEllipsis}>…</span>
      : entries.slice(0, limit).map(([key, item], index) => (
        <span key={key}>
          {index > 0 && <span className={css.punctuation}>, </span>}
          {!array && <><span className={css.label}>{JSON.stringify(key)}</span><span className={css.punctuation}>: </span></>}
          {previewValue(item, depth + 1)}
        </span>
      ))}
    {depth < PREVIEW_DEPTH_LIMIT && entries.length > limit && <span className={css.previewEllipsis}>, …</span>}
    <span className={css.punctuation}>{close}</span>
  </>;
}

type RenderCopy = (field: string, value: unknown) => ReactNode;

function JsonTreeNode({ field, value, renderCopy }: { field: string; value: unknown; renderCopy: RenderCopy }) {
  const entries = isContainer(value) ? entriesOf(value) : [];
  const label = <><span className={css.label}>{JSON.stringify(field)}</span><span className={css.punctuation}>: </span></>;
  return <li className={css.row}>
    {entries.length > 0 ? (
      <details className={css.branch}>
        <summary>{label}{previewValue(value)}</summary>
        <ul className={css.children}>
          {entries.map(([key, child]) => <JsonTreeNode key={key} field={key} value={child} renderCopy={renderCopy} />)}
        </ul>
      </details>
    ) : <span className={css.leaf}>{label}{previewValue(value)}</span>}
    {renderCopy(field, value)}
  </li>;
}

/** Receives a sanitized JSON projection from its owner. */
export function JsonTree({ data, label, renderCopy }: { data: object; label: string; renderCopy: RenderCopy }) {
  const [open, close] = bracketOf(data);
  return <div className={css.root} data-json-tree aria-label={label} role="group">
    <span className={css.punctuation}>{open}</span>
    <ul className={css.children}>
      {entriesOf(data).map(([key, value]) => <JsonTreeNode key={key} field={key} value={value} renderCopy={renderCopy} />)}
    </ul>
    <span className={css.punctuation}>{close}</span>
  </div>;
}
