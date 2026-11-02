/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-sidebar-documentpreview/src/client/html/bytes.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: static image binary payload extension and local byte-reader type.
 * See integrations/deepseek-harness/LICENSE.
 */
/** UTF-8 decoding for file bytes and encoding only for the iframe's script payload. */

const BASE64_CHUNK_BYTES = 0x8000

/**
 * Decode complete UTF-8 text, rejecting invalid byte sequences.
 * @param data - complete UTF-8 bytes.
 * @returns decoded text; invalid UTF-8 throws.
 */
export function decodeText(data: Uint8Array<ArrayBuffer>): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(data)
}

/**
 * Encode Unicode text for the iframe's base64 payload.
 * @param text - Unicode text.
 * @returns base64 of its UTF-8 bytes.
 */
export function encodeText(text: string): string {
  return encodeBytes(new TextEncoder().encode(text))
}

/** Encode already-read binary bytes without attempting UTF-8 decoding. */
export function encodeBytes(bytes: Uint8Array<ArrayBuffer>): string {
  const chunks: string[] = []
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK_BYTES) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK_BYTES)))
  }
  return btoa(chunks.join(''))
}
