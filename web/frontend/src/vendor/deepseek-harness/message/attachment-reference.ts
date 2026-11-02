/** Easel security boundary for uploaded references shown by the Harness message surface. */
import { mediaUrl } from '../../../lib/api';

/** A stored reference may only resolve through the current IP's guarded media route. */
export function attachmentHref(path: string): string | null {
  if (!path || path.startsWith('/') || Array.from(path).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
      || /^[a-z][a-z\d+.-]*:/i.test(path) || /%(?:2f|5c|2e|00)/i.test(path)
      || path.split('/').some(part => !part || part === '.' || part === '..')) return null;
  return mediaUrl(path);
}
