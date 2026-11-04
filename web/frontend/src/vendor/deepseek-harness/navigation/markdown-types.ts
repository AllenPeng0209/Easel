/** Structural event subset used by the unmodified upstream Markdown converter. */
export interface FileAttachmentRef { name?: string; bytes: number }
export interface ImageAttachmentRef extends FileAttachmentRef { width: number; height: number }
type ContentBlock = { type: 'text'; text: string } | { type: 'image'; attachment: ImageAttachmentRef }
  | { type: 'file'; attachment: FileAttachmentRef } | { type: 'tool-call' };
export interface UserMessage { content: ContentBlock[] }
export interface AssistantMessage { content: ContentBlock[] }
export interface ToolResultMessage { toolCallId: string; content: ContentBlock[]; isError?: boolean }
export type SessionEvent =
  | { type: 'session/title'; data: { title: string } }
  | { type: 'turn/start' | 'turn/end' }
  | { type: 'user/message'; surfaceOp: string; data: { source: { kind: string }; content: ContentBlock[] } }
  | { type: 'assistant/message'; surfaceOp: string; data: { message: AssistantMessage } }
  | { type: 'tool/call'; data: { name: string; arguments: string; callId: string } }
  | { type: 'tool/result'; surfaceOp: string; data: { message: ToolResultMessage; error?: { name: string; code: string; reason?: string } } };
