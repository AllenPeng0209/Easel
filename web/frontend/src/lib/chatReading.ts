import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { projectStorageKey } from './projectScope';
import { scrollMetrics, useScrollFollow } from '../vendor/deepseek-harness/composer/use-scroll-follow';

/** Easel binding for the upstream follow controller; only reader intent releases the tail. */
export function useChatReading(sessionId: string, changeToken: unknown, enabled: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const follow = useScrollFollow(true, 25);
  const [following, setFollowing] = useState(true);
  const [unread, setUnread] = useState(false);
  const restored = useRef(false);
  const lastToken = useRef(changeToken);
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
    else if (lastToken.current !== changeToken) setUnread(true);
    lastToken.current = changeToken;
  }, [enabled, changeToken, follow, backToBottom]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!enabled || !element) return;
    const release = follow.bind(element);
    const settle = () => { setFollowing(follow.settle(scrollMetrics(element))); remember(); };
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
  const onWheel = (event: React.WheelEvent) => {
    interrupt();
    if (event.deltaY < 0) { follow.setFollowing(false); setFollowing(false); }
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (['PageUp', 'Home', 'ArrowUp'].includes(event.key)) {
      interrupt(); follow.setFollowing(false); setFollowing(false);
    }
  };
  return { scrollRef, threadRef, following, unread, backToBottom, onScroll, onWheel, onKeyDown, interrupt };
}
