/**
 * Xiangya binding for the pinned DeepSeek incremental Markdown renderer.
 * The existing sanitizer, immutable media policy and settled discovery still own all output.
 */
import { useMemo, useRef, useState } from 'react';
import { extractArtifacts, mergeArtifacts, type Artifact } from '../lib/artifacts';
import { pinSnapshotLinks, type ChatDeliverable } from '../lib/deliverables';
import { externalizeMediaLinks, linkifyOutputs } from '../lib/linkifyOutputs';
import { sanitizeMarkdownHtml } from '../lib/sanitize';
import { IncrementalMarkdownRenderer, type MarkdownBlock } from '../vendor/deepseek-harness/markdown/incremental';

interface ProcessedBlock extends MarkdownBlock {
  artifacts: Artifact[];
  archives: string[];
}

export function useAnswerMarkdown(source: string, streaming: boolean, declared: ChatDeliverable[], blockedPaths: string[]) {
  const [renderer] = useState(() => new IncrementalMarkdownRenderer({ sanitize: sanitizeMarkdownHtml }));
  const processed = useRef(new WeakMap<MarkdownBlock, { policy: string; block: ProcessedBlock }>());
  const previousBlocks = useRef<readonly MarkdownBlock[]>([]);
  const input = useMemo(() => streaming ? source : linkifyOutputs(source), [source, streaming]);
  const policy = useMemo(() => JSON.stringify([streaming, blockedPaths, declared
    .filter(file => file.storageMode === 'snapshot')
    .map(file => [file.artifactId, file.projectId, file.path, file.url, file.name])]), [blockedPaths, declared, streaming]);

  return useMemo(() => {
    const blocks = renderer.update(input, !streaming).map(block => {
      const cached = processed.current.get(block);
      if (cached?.policy === policy) return cached.block;
      const html = pinSnapshotLinks(externalizeMediaLinks(block.html), declared, blockedPaths, !streaming);
      const view = { key: block.key, ...(streaming || !html ? { html, artifacts: [], archives: [] } : extractArtifacts(html)) };
      processed.current.set(block, { policy, block: view });
      return view;
    });
    const previous = previousBlocks.current;
    const unchanged = blocks.length === previous.length && blocks.every((block, index) =>
      block.key === previous[index].key && block.html === previous[index].html);
    if (!unchanged) previousBlocks.current = blocks;
    return {
      blocks: previousBlocks.current,
      artifacts: mergeArtifacts(blocks.flatMap(block => block.artifacts)),
      archives: [...new Set(blocks.flatMap(block => block.archives))],
    };
  }, [blockedPaths, declared, input, policy, renderer, streaming]);
}
