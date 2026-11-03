/**
 * Keyed Tool view selection adapted from DeepSeek Harness
 * ui-tool/src/client/{contract/slots.ts,tool/ToolCallTree.tsx} @ ce7073d (MIT).
 * A wire name selects the candidate, and the typed Host guard admits its view.
 */
import { createElement, type ReactNode } from 'react';
import {
  commandStatus, fileMutationLabel, fileMutationState, isCommandExecution, type AgentEvent,
} from '../lib/agentEvents';
import { WebBlock } from '../vendor/deepseek-harness/tool/WebBlock';
import { webActionTitle, webCardModel } from '../vendor/deepseek-harness/tool/web-card-model';
import CommandExecutionCard from './CommandExecutionCard';
import FileMutationCard from './FileMutationCard';
import ToolValueInspector from './ToolValueInspector';

export interface ToolRendererView {
  title: string;
  status?: string;
  statusLabel?: string;
  unconfirmed?: boolean;
  suppressDerivedDuration?: boolean;
  details: (showTerminalFacts: boolean) => ReactNode;
}

type ProjectToolRenderer = (event: AgentEvent, settled: boolean) => ToolRendererView | undefined;

export interface ToolRenderer {
  key: string;
  names: readonly string[];
  project: ProjectToolRenderer;
}

export type ToolRendererResolution = { kind: 'specialized'; key: string; view: ToolRendererView }
  | { kind: 'generic'; failed: boolean };

/** Register trusted application renderers only. Unclaimed names stay generic. */
export function createToolRendererRegistry(renderers: readonly ToolRenderer[]) {
  const byName = new Map<string, ToolRenderer>();
  for (const renderer of renderers) {
    for (const name of renderer.names) byName.set(name, renderer);
  }
  return (event: AgentEvent, settled: boolean): ToolRendererResolution => {
    try {
      if (event.kind === 'tool' && typeof event.name === 'string') {
        const renderer = byName.get(event.name);
        const view = renderer?.project(event, settled);
        if (renderer && view) return { kind: 'specialized', key: renderer.key, view };
      }
      return { kind: 'generic', failed: false };
    } catch {
      return { kind: 'generic', failed: true };
    }
  };
}

export const resolveToolRenderer = createToolRendererRegistry([
  {
    key: 'bash', names: ['bash', 'commandExecution'],
    project: event => !isCommandExecution(event) ? undefined : {
      title: '运行命令', status: commandStatus(event, event.status || ''), suppressDerivedDuration: true,
      details: showExit => createElement(CommandExecutionCard, { event, showExit }),
    },
  },
  {
    key: 'apply_patch', names: ['apply_patch'],
    project: (event, settled) => {
      const state = fileMutationState(event, settled);
      return state === undefined ? undefined : {
        title: '修改文件', statusLabel: fileMutationLabel[state], unconfirmed: state === 'unconfirmed',
        details: () => createElement(FileMutationCard, { event, state }),
      };
    },
  },
  {
    key: 'web_search', names: ['web_search'],
    project: event => {
      const model = webCardModel(event);
      return model === undefined ? undefined : {
        title: webActionTitle[model.action], suppressDerivedDuration: true,
        details: () => createElement(WebBlock, {
          model, renderResults: value => createElement(ToolValueInspector, { label: '引擎结果记录', value }),
        }),
      };
    },
  },
]);
