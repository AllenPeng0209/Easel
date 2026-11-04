/**
 * Keyed Tool view selection adapted from DeepSeek Harness
 * ui-tool/src/client/{contract/slots.ts,tool/ToolCallTree.tsx} @ ce7073d (MIT).
 * A wire name selects the candidate, and the typed Host guard admits its view.
 */
import { createElement, type ReactNode } from 'react';
import {
  agentToolKey, commandStatus, fileMutationLabel, fileMutationState, isCommandExecution, nativePublicationToolKey, type AgentEvent,
} from '../lib/agentEvents';
import { WebBlock } from '../vendor/deepseek-harness/tool/WebBlock';
import { webActionTitle, webCardModel } from '../vendor/deepseek-harness/tool/web-card-model';
import { businessObjectIdentity, type BusinessObject } from '../lib/businessObjects';
import { fetchBusinessObject } from '../lib/businessObjectApi';
import { fetchPublicationApproval } from '../lib/api';
import type { PublicationApprovalRecord } from '../lib/publicationApproval';
import { publicationReceiptIdentity } from '../lib/publicationReceipt';
import PublicationReceiptCard from './PublicationReceiptCard';
import BusinessObjectCard from './BusinessObjectCard';
import CommandExecutionCard from './CommandExecutionCard';
import FileMutationCard from './FileMutationCard';
import ToolValueInspector from './ToolValueInspector';
import { selectChatWorkshopRender } from '../lib/workshop';
import type { WorkshopRenderReceipt } from '../lib/workshop';

export interface ToolRendererView {
  title: string;
  status?: string;
  statusLabel?: string;
  unconfirmed?: boolean;
  suppressDerivedDuration?: boolean;
  workshopRender?: { projectId: string; initial: WorkshopRenderReceipt };
  details: (showTerminalFacts: boolean) => ReactNode;
}

export interface ToolRendererRecords {
  workshops?: BusinessObject[];
  publications?: PublicationApprovalRecord[];
}

type ProjectToolRenderer = (
  event: AgentEvent, settled: boolean, records?: ToolRendererRecords,
) => ToolRendererView | undefined;

export function businessToolCallKey(event: AgentEvent): string | undefined {
  return event.name === 'easel_business_objects' &&
      typeof event.runId === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(event.runId) &&
      typeof event.callId === 'string' && !!event.callId.trim() && event.callId.length <= 256 && !/\p{Cc}/u.test(event.callId)
    ? agentToolKey(event) : undefined;
}

export function nativeBusinessToolKey(event: AgentEvent): string | undefined {
  return event.kind === 'tool' && event.nativeBusinessTool === true ? businessToolCallKey(event) : undefined;
}

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
  return (event: AgentEvent, settled: boolean, records?: ToolRendererRecords): ToolRendererResolution => {
    try {
      if (event.kind === 'tool' && typeof event.name === 'string') {
        const renderer = byName.get(event.name);
        const view = renderer?.project(event, settled, records);
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
  {
    key: 'workshop', names: ['easel_business_objects'],
    project: (event, _settled, records) => {
      const workshops = records?.workshops;
      if (!nativeBusinessToolKey(event) || !workshops?.length) return undefined;
      const workshopRender = workshops.map(record => {
        const project = record.details.project;
        if (!project || typeof project !== 'object' || Array.isArray(project)) return undefined;
        const initial = selectChatWorkshopRender(project as Record<string, unknown>, record.id, record.version.revision);
        return initial ? { projectId: record.id, initial } : undefined;
      }).find(Boolean);
      return {
        title: '视频工坊',
        workshopRender,
        details: () => createElement('div', { 'data-tool-workshop-records': true }, workshops.map(record =>
          createElement(BusinessObjectCard, { key: businessObjectIdentity(record), record, onRefresh: fetchBusinessObject }))),
      };
    },
  },
  {
    key: 'publication', names: ['easel_publish', 'easel_publication_status'],
    project: (event, _settled, records) => {
      const publications = records?.publications;
      return !nativePublicationToolKey(event) || !publications?.length ? undefined : {
        title: event.name === 'easel_publish' ? '发布调用' : '查询发布状态',
        details: () => createElement('div', { 'data-tool-publication-receipts': true }, publications.map(record =>
          createElement(PublicationReceiptCard, {
            key: JSON.stringify([publicationReceiptIdentity(record), record.operationId]), record, onRefresh: fetchPublicationApproval,
          }))),
      };
    },
  },
]);
