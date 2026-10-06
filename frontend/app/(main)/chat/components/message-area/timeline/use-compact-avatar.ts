import { useEffect, useState } from 'react';
import { AVATAR_SIZE_DESKTOP, AVATAR_SIZE_MOBILE } from './message-avatar';

const QUERY = '(max-width: 640px)';

/** 20 px avatars at 640 px and below, 24 px above. */
export function useAvatarSize(): number {
  const [small, setSmall] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(QUERY);
    setSmall(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setSmall(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return small ? AVATAR_SIZE_MOBILE : AVATAR_SIZE_DESKTOP;
}
