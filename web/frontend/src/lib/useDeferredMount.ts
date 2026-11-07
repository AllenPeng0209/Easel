import { useEffect, useState } from 'react';

/** Give navigation a chance to settle before starting an expensive first surface. */
export function useDeferredMount(active: boolean): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!active || ready) return;
    const timer = setTimeout(() => setReady(true), 800);
    return () => clearTimeout(timer);
  }, [active, ready]);
  return active && ready;
}
