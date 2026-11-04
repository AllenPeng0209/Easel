import type { Plugin } from 'vite';

export interface AssetArchiveResult {
  copied: number;
  existing: number;
}

export const FRONTEND_ASSET_ARCHIVE: string;
export function archiveAssetDirectory(sourceAssets: string, archive?: string): Promise<AssetArchiveResult>;
export function archiveBundleAssets(outputDirectory: string, bundle: Record<string, unknown>, archive?: string): Promise<AssetArchiveResult>;
export function assetArchivePlugin(): Plugin;
