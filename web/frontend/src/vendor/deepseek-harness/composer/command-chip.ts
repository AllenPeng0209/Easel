/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from DeepSeek Harness ui-conversation input/editor/command-chip-node.tsx
 * and command-chip-selection.ts @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel uses a Host-reviewed command ID and the visible canonical token.
 */
import { $applyNodeReplacement, DecoratorNode, type NodeKey, type SerializedLexicalNode, type Spread } from 'lexical';
import { validateChatCommand, type ChatCommand } from '../../../lib/chatCommands';

type SerializedCommand = Spread<ChatCommand, SerializedLexicalNode>;

export class CommandChipNode extends DecoratorNode<null> {
  __command: ChatCommand;
  static override getType(): string { return 'command-chip'; }
  static override clone(node: CommandChipNode): CommandChipNode { return new CommandChipNode(node.__command, node.__key); }
  static override importJSON({ id, token }: SerializedLexicalNode & Record<string, unknown>): CommandChipNode {
    const command = typeof token === 'string' ? validateChatCommand(token, { id, token }) : null;
    if (!command) throw new Error('Invalid command chip');
    return $createCommandChip(command);
  }

  constructor(command: ChatCommand = { id: '', token: '' }, key?: NodeKey) {
    super(key);
    this.__command = { id: command.id, token: command.token };
  }

  override exportJSON(): SerializedCommand { return { ...super.exportJSON(), ...this.__command }; }
  override createDOM(): HTMLElement {
    const element = document.createElement('span');
    element.setAttribute('data-composer-chip', 'command');
    element.setAttribute('contenteditable', 'false');
    element.textContent = this.__command.token;
    return element;
  }

  override updateDOM(): false { return false; }
  override isInline(): true { return true; }
  override isKeyboardSelectable(): false { return false; }
  override decorate(): null { return null; }
  override getTextContent(): string { return this.getLatest().__command.token; }
  getCommand(): ChatCommand { return this.getLatest().__command; }
}

export function $createCommandChip(command: ChatCommand): CommandChipNode {
  return $applyNodeReplacement(new CommandChipNode(command));
}
