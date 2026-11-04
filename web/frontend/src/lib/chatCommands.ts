/** A deliberately selected command at the beginning of the visible draft. */
export interface ChatCommand {
  id: string;
  token: string;
}

export interface ChatCommandOption extends ChatCommand {
  label: string;
  group: string;
  description: string;
  argumentHint: string;
}

/** Check the local claim's shape and position. The host owns command availability. */
export function validateChatCommand(text: string, value: unknown): ChatCommand | null {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== 2 || !Object.hasOwn(value, 'id') || !Object.hasOwn(value, 'token')) return null;
  const { id, token } = value as Record<string, unknown>;
  if (typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(id) ||
      typeof token !== 'string' || !/^\/[\p{L}\p{N}\p{M}_-]{1,64}$/u.test(token) ||
      !text.startsWith(token)) return null;
  if (text.length > token.length && !/\s/u.test(text[token.length])) return null;
  return { id, token };
}

export function chatCommandOverlapsReferences(
  command: ChatCommand, fileReferences?: readonly { start: number }[], sessionReferences?: readonly { start: number }[],
): boolean {
  const overlaps = ({ start }: { start: number }) => start < command.token.length;
  return Boolean(fileReferences?.some(overlaps) || sessionReferences?.some(overlaps));
}
