import { useEffect, useState } from 'react';
import { canRetryChatWorkshopObservation, chatWorkshopRenderState, observeChatWorkshopRender, workshopCancellationLabel, workshopProgress, type WorkshopRenderReceipt } from '../lib/workshop';

// Visibility-owned observation adapted from DeepSeek Harness JobListAction.tsx
// (ui-jobs @ ce7073d, MIT). 响芽 polls only the exact persisted render receipt.
export default function WorkshopRenderObservation({ projectId, initial }: {
  projectId: string;
  initial: WorkshopRenderReceipt;
}) {
  // The parent keys this observer by its exact persisted receipt version.
  const [seed] = useState(initial);
  const [latest, setLatest] = useState(seed);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let receipt = seed;
    let retries = 0;
    let rejected = false;
    let activeRequest: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pause = () => {
      clearTimeout(timer);
      activeRequest?.abort();
      activeRequest = undefined;
    };
    const advance = async () => {
      if (rejected || document.visibilityState === 'hidden' || activeRequest || !chatWorkshopRenderState(receipt).active) return;
      const request = new AbortController();
      activeRequest = request;
      try {
        const next = await observeChatWorkshopRender(projectId, receipt, request.signal);
        if (request.signal.aborted) return;
        receipt = next;
        retries = 0;
        setLatest(next);
        setUnavailable(false);
        if (chatWorkshopRenderState(next).active) timer = setTimeout(() => void advance(), 5000);
      } catch (error) {
        if (request.signal.aborted) return;
        setUnavailable(true);
        rejected = !canRetryChatWorkshopObservation(error);
        if (!rejected && retries < 3) {
          timer = setTimeout(() => void advance(), 5000 * 2 ** retries);
          retries += 1;
        }
      } finally {
        if (activeRequest === request) activeRequest = undefined;
      }
    };
    const visibility = () => {
      pause();
      if (document.visibilityState !== 'hidden') void advance();
    };
    document.addEventListener('visibilitychange', visibility);
    void advance();
    return () => {
      pause();
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [seed, projectId]);
  const state = chatWorkshopRenderState(latest);
  const progress = workshopProgress(latest);
  const cancellation = workshopCancellationLabel(latest);
  return <span className="agent-tool__workshop-render" role="status"
    data-render-observation={latest.id} data-render-state={state.code}>
     · {state.label}{state.active && <> · {progress === null ? '进度未知' : `${Math.round(progress * 100)}%`}</>}
    {cancellation && <span data-render-cancellation={latest.cancellation?.status}> · {cancellation}</span>}
    {unavailable && <> · 暂时无法更新，显示上次记录</>}
  </span>;
}
