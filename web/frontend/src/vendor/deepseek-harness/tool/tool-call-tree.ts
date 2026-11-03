/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: packages/client/ui-chat/src/client/conversation-nodes/tool.ts and
 * packages/client/ui-chat/src/client/model/tool-call-tree.ts.
 * Easel supplies scoped keys and flat live/persisted snapshots.
 * License: integrations/deepseek-harness/LICENSE.
 */
const MAX_DEPTH = 256;

export interface ToolCallBranch<T> {
  key: string;
  value: T;
  children: ToolCallBranch<T>[];
}

function acceptsEdge<T>(
  parentKey: string, child: ToolCallBranch<T>, parents: ReadonlyMap<string, string>,
): boolean {
  let parentDepth = 0;
  let cursor: string | undefined = parentKey;
  while (cursor !== undefined) {
    if (cursor === child.key) return false;
    parentDepth++;
    cursor = parents.get(cursor);
  }
  const pending = [{ branch: child, depth: 1 }];
  for (const { branch, depth } of pending) {
    if (parentDepth + depth > MAX_DEPTH) return false;
    for (const nested of branch.children) pending.push({ branch: nested, depth: depth + 1 });
  }
  return true;
}

/** Index every call before attaching edges so late parents preserve earlier children. */
export function projectToolCallTree<T>(entries: readonly { key: string; value: T; parentKey?: string }[]) {
  const calls = new Map<string, ToolCallBranch<T>>(entries.map(({ key, value }) =>
    [key, { key, value, children: [] }]));
  const parents = new Map<string, string>();
  for (const { key, parentKey } of entries) {
    const parent = parentKey === undefined ? undefined : calls.get(parentKey);
    const child = calls.get(key)!;
    if (!parent || parents.has(key) || !acceptsEdge(parent.key, child, parents)) continue;
    parent.children.push(child);
    parents.set(key, parent.key);
  }
  return { calls, parents };
}
