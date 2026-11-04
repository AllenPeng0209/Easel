/** Immutable ZIP transport. Member authority is the original parent ID + index. */
import type { AgentArtifact } from './agentEvents';
import type { Artifact } from './artifacts';
import { projectFetch, projectUrl } from './projectScope';

export interface SnapshotArchiveFile {
  index: number;
  name: string;
  path: string;
  mime: string;
  size: number;
  kind: string;
  url: string;
  preview: Artifact | null;
}
type SnapshotArchiveSource = Pick<AgentArtifact, 'artifactId' | 'url' | 'storageMode' | 'status'>;

const PREVIEW_KINDS = new Set<Artifact['kind']>(['image', 'html', 'video', 'audio', 'pdf']);
const invalid = () => new Error('ZIP 成员索引无效，无法安全预览');

/** Reject aliases before URL parsing can collapse traversal or encoded indexes. */
export function snapshotArchiveContentUrl(artifact: SnapshotArchiveSource): string {
  if (artifact.storageMode !== 'snapshot' || artifact.status !== 'available'
      || !/^[a-f0-9]{32}$/.test(artifact.artifactId)) throw invalid();
  const expected = projectUrl(`/api/chat/artifacts/${artifact.artifactId}/content`);
  if (projectUrl(artifact.url) !== expected) throw invalid();
  return expected;
}

function unsafeCharacters(value: string): boolean {
  return Array.from(value).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
}

function safeMemberPath(path: unknown): path is string {
  return typeof path === 'string' && !!path && !/^(?:[a-z][a-z\d+.-]*:|[/\\])/iu.test(path)
    && !unsafeCharacters(path)
    && path.split('/').every(part => !!part && part !== '.' && part !== '..');
}

/** All listed files retain server identity, including formats without a preview. */
export function normalizeSnapshotArchiveFiles(artifact: SnapshotArchiveSource, payload: unknown): SnapshotArchiveFile[] {
  const root = snapshotArchiveContentUrl(artifact).replace(/\/content$/, '/archive/');
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { files?: unknown }).files)) throw invalid();
  const seen = new Set<number>();
  return (payload as { files: unknown[] }).files.map(value => {
    if (!value || typeof value !== 'object') throw invalid();
    const file = value as Record<string, unknown>;
    if (!Number.isSafeInteger(file.index) || (file.index as number) < 0 || seen.has(file.index as number)
        || !safeMemberPath(file.path) || typeof file.name !== 'string' || !file.name
        || file.name !== file.path.split('/').at(-1) || file.name.includes('/') || unsafeCharacters(file.name)
        || typeof file.mime !== 'string' || !file.mime || typeof file.kind !== 'string' || !file.kind
        || !Number.isSafeInteger(file.size) || (file.size as number) < 0 || typeof file.url !== 'string') throw invalid();
    const url = root + file.index + '/content';
    if (projectUrl(file.url) !== url) throw invalid();
    seen.add(file.index as number);
    const preview: Artifact | null = PREVIEW_KINDS.has(file.kind as Artifact['kind'])
      ? { url, name: file.name, kind: file.kind as Artifact['kind'], storageMode: 'snapshot', sourcePath: file.path } : null;
    return { index: file.index as number, name: file.name, path: file.path, mime: file.mime,
      size: file.size as number, kind: file.kind, url, preview };
  });
}

export async function loadSnapshotArchive(artifact: SnapshotArchiveSource, signal: AbortSignal): Promise<SnapshotArchiveFile[]> {
  const endpoint = snapshotArchiveContentUrl(artifact).replace(/\/content$/, '/archive');
  const response = await projectFetch(endpoint, { signal, redirect: 'error' });
  signal.throwIfAborted();
  let payload: unknown;
  try { payload = await response.json(); } catch {
    throw Object.assign(new Error(`ZIP 索引读取失败（${response.status}）：响应格式无效`), { status: response.status });
  }
  signal.throwIfAborted();
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? (payload as { error?: unknown }).error : undefined;
    throw Object.assign(new Error(typeof detail === 'string' && detail ? detail : `ZIP 索引读取失败（${response.status}）`),
      { status: response.status });
  }
  return normalizeSnapshotArchiveFiles(artifact, payload);
}
