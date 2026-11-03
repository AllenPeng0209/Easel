import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { projectStorageKey } from './projectScope';
import type { ChatMessage, StreamState } from './store';
import type { AgentEvent } from './agentEvents';
import { scrollMetrics, useScrollFollow } from '../vendor/deepseek-harness/composer/use-scroll-follow';

function eventContent(event: AgentEvent) {
  if (event.business) return [event.kind, event.callId, event.business.id, event.business.snapshotHash];
  const { id: _id, timestamp: _timestamp, startedAt: _startedAt, completedAt: _completedAt, ...content } = event;
  return content;
}

function messageContent({ role, content, thinking, attachments, questions, events }: ChatMessage): string | null {
  if (!content && !thinking && !attachments?.length && !questions?.length && !events?.length) return null;
  return JSON.stringify([role, content || '', thinking || '', attachments || [], questions || [], events?.map(eventContent) || []]);
}

function nestedScrollConsumes(target: EventTarget, scrollport: HTMLElement, direction: number): boolean {
  const node = target as Node;
  for (let element = node.nodeType === 1 ? node as HTMLElement : node.parentElement;
    element && element !== scrollport; element = element.parentElement) {
    const { top, floor } = scrollMetrics(element);
    if (floor <= 0) continue;
    const style = window.getComputedStyle(element);
    if (!['auto', 'scroll', 'overlay'].includes(style.overflowY)) continue;
    if (style.overscrollBehaviorY === 'contain' || style.overscrollBehaviorY === 'none') return true;
    if (direction < 0 ? top > 0.5 : top < floor - 0.5) return true;
  }
  return false;
}

/** Easel binding for the upstream follow controller; only reader intent releases the tail. */
export function useChatReading(sessionId: string, messages: readonly ChatMessage[], stream: StreamState | undefined, enabled: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const follow = useScrollFollow(true, 25);
  const [following, setFollowing] = useState(true);
  const [unread, setUnread] = useState(false);
  const restored = useRef(false);
  const history = useMemo(() => messages.map(messageContent).filter(token => token !== null), [messages]);
  const { content, thinking, questions, events } = stream || {};
  const live = useMemo(() => messageContent({ role: 'assistant', content: content || '', thinking, questions, events }),
    [content, thinking, questions, events]);
  const transcript = useMemo(() => live === null ? history : [...history, live], [history, live]);
  const lastTranscript = useRef(transcript);
  const key = projectStorageKey(`easel_chat_reading:${sessionId}`);

  const remember = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    try { sessionStorage.setItem(key, JSON.stringify({ following: follow.active, top: element.scrollTop })); } catch { /* Nonessential reading memory. */ }
  }, [key, follow]);
  const backToBottom = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    follow.toBottom(element, scrollMetrics(element), 'instant');
    setFollowing(true);
    setUnread(false);
    remember();
  }, [follow, remember]);
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!enabled || !element || restored.current) return;
    restored.current = true;
    let saved: { following?: boolean; top?: number } | null = null;
    try { saved = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch { /* Follow tail on corrupt memory. */ }
    if (saved?.following === false && typeof saved.top === 'number') {
      follow.jump(element, scrollMetrics(element), saved.top);
      setFollowing(follow.active);
    } else backToBottom();
  }, [enabled, key, follow, backToBottom]);
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!enabled || !element) return;
    if (follow.active) backToBottom();
    else if (transcript.some((item, index) => item !== lastTranscript.current[index])) setUnread(true);
    lastTranscript.current = transcript;
  }, [enabled, transcript, follow, backToBottom]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!enabled || !element) return;
    const release = follow.bind(element);
    const settle = () => {
      setFollowing(follow.settle(scrollMetrics(element)));
      if (follow.active) setUnread(false);
      remember();
    };
    element.addEventListener('scrollend', settle);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => {
      if (follow.active) backToBottom();
    }) : null;
    if (threadRef.current) observer?.observe(threadRef.current);
    return () => { remember(); release(); element.removeEventListener('scrollend', settle); observer?.disconnect(); };
  }, [enabled, follow, remember, backToBottom]);

  const onScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    setFollowing(follow.sample(scrollMetrics(element)));
    if (follow.active) setUnread(false);
    remember();
  };
  const interrupt = () => {
    const element = scrollRef.current;
    if (element) follow.interrupt(element, scrollMetrics(element));
  };
  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.ctrlKey || event.deltaY === 0
      || nestedScrollConsumes(event.target, event.currentTarget, event.deltaY)) return;
    interrupt();
    if (event.deltaY < 0 && event.currentTarget.scrollTop > 0) { follow.setFollowing(false); setFollowing(false); }
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (!event.defaultPrevented && ['PageUp', 'Home', 'ArrowUp'].includes(event.key)
      && event.currentTarget.scrollTop > 0 && !target.closest('input, textarea, select, [contenteditable]')
      && !nestedScrollConsumes(target, event.currentTarget, -1)) {
      interrupt(); follow.setFollowing(false); setFollowing(false);
    }
  };
  return { scrollRef, threadRef, following, unread, backToBottom, onScroll, onWheel, onKeyDown, interrupt };
}
