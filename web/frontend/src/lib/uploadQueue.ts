/**
 * Adapted from DeepSeek Harness's beginFileUpload/pumpFileUploads lifecycle.
 * Copyright (c) 2026 DeepSeek. MIT; see integrations/deepseek-harness/LICENSE.
 * Source: packages/client/ui-conversation/src/client/service.ts:378-454
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447
 * Easel adaptation: per-file REST receipts, ordered placeholders, abort handles.
 */
import type { UploadedFile } from './api';

export interface UploadAttachment {
  key: string;
  name: string;
  size: number;
  mime: string;
  status: 'queued' | 'uploading' | 'ready' | 'failed';
  reference?: UploadedFile;
  error?: string;
  previewUrl?: string;
}

type Transport = (file: File, signal: AbortSignal) => Promise<UploadedFile>;

/** Upload ownership is independent of a batch's completion or React renders. */
export class AttachmentUploadQueue {
  private items: UploadAttachment[];
  private readonly files = new Map<string, File>();
  private readonly operations = new Map<string, AbortController>();
  private activeFileUploads = 0;
  private disposed = false;
  private readonly transport: Transport;
  private readonly onChange: (items: UploadAttachment[]) => void;
  private readonly maxConcurrentFileUploads: number;

  constructor(transport: Transport, onChange: (items: UploadAttachment[]) => void,
    initial: UploadAttachment[] = [], concurrency = 2) {
    this.transport = transport;
    this.onChange = onChange;
    this.items = initial.map(item => ({ ...item }));
    this.maxConcurrentFileUploads = Math.max(1, concurrency);
  }

  get snapshot(): UploadAttachment[] { return this.items.map(item => ({ ...item })); }
  get busy(): boolean { return this.items.some(item => item.status === 'queued' || item.status === 'uploading'); }
  get ready(): UploadedFile[] {
    return this.items.flatMap(item => item.status === 'ready' && item.reference ? [item.reference] : []);
  }

  add(files: readonly File[]): void {
    if (this.disposed) return;
    for (const file of files) {
      const key = crypto.randomUUID();
      this.files.set(key, file);
      const previewUrl = file.type.startsWith('image/') && typeof URL.createObjectURL === 'function'
        ? URL.createObjectURL(file) : undefined;
      this.items.push({ key, name: file.name, size: file.size, mime: file.type,
        status: 'queued', ...(previewUrl ? { previewUrl } : {}) });
    }
    this.publish();
    this.pumpFileUploads();
  }

  retry(key: string): void {
    const item = this.items.find(value => value.key === key);
    if (!item || item.status !== 'failed' || !this.files.has(key) || this.disposed) return;
    item.status = 'queued';
    item.error = undefined;
    this.publish();
    this.pumpFileUploads();
  }

  remove(key: string): void {
    this.operations.get(key)?.abort();
    this.operations.delete(key);
    this.files.delete(key);
    const item = this.items.find(value => value.key === key);
    if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
    this.items = this.items.filter(value => value.key !== key);
    this.publish();
  }

  clear(): void {
    for (const item of [...this.items]) this.remove(item.key);
  }

  /** This owner is disposed when its session view unmounts. */
  dispose(): void {
    this.disposed = true;
    for (const operation of this.operations.values()) operation.abort();
    for (const item of this.items) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    this.operations.clear();
    this.files.clear();
  }

  private publish(): void { if (!this.disposed) this.onChange(this.snapshot); }

  /** Start queued upload workers until the configured concurrency is occupied. */
  private pumpFileUploads(): void {
    while (!this.disposed && this.activeFileUploads < this.maxConcurrentFileUploads) {
      const item = this.items.find(value => value.status === 'queued');
      if (!item) return;
      const file = this.files.get(item.key);
      if (!file) return;
      const controller = new AbortController();
      this.operations.set(item.key, controller);
      item.status = 'uploading';
      this.activeFileUploads += 1;
      this.publish();
      // Transport starts immediately, while placeholders keep their original order.
      let task: Promise<UploadedFile>;
      try { task = this.transport(file, controller.signal); }
      catch (error) { task = Promise.reject(error); }
      void task.then(reference => {
        if (this.disposed || this.operations.get(item.key) !== controller || controller.signal.aborted) return;
        if (!reference?.id || !reference.path || !reference.name) throw new Error('上传完成但未收到有效文件回执');
        item.reference = { ...reference, size: file.size, mime: file.type };
        item.status = 'ready';
      }).catch(error => {
        if (this.disposed || this.operations.get(item.key) !== controller) return;
        item.status = 'failed';
        item.error = error && typeof error === 'object' && typeof error.message === 'string'
          ? error.message : String(error);
      }).finally(() => {
        if (this.operations.get(item.key) === controller) this.operations.delete(item.key);
        this.activeFileUploads -= 1;
        this.publish();
        this.pumpFileUploads();
      });
    }
  }
}
