/**
 * Local disclosure ownership adapted from DeepSeek Harness ui-chat's
 * use-disclosure and presentation-policy @ ce7073d (MIT).
 * Automatic collapse waits until keyboard focus leaves an open section.
 */
import { useState, type FocusEvent } from 'react';

export function useWorkDisclosure(openByPolicy: boolean, automatic: boolean) {
  const [focused, setFocused] = useState(false);
  const open = openByPolicy || (automatic && focused);
  return {
    open,
    onFocusCapture: () => { if (open && automatic) setFocused(true); },
    onBlurCapture: (event: FocusEvent<HTMLElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
    },
  };
}
