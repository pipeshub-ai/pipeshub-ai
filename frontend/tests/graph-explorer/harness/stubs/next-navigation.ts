import { useEffect, useMemo, useState } from 'react';

// Re-renders on Back and Forward and on the page's own writes, as the real router does.
const writes = new Set<() => void>();
const notify = () => writes.forEach((listener) => listener());

function useSearch(): string {
  const [, bump] = useState(0);
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    window.addEventListener('popstate', on);
    writes.add(on);
    return () => {
      window.removeEventListener('popstate', on);
      writes.delete(on);
    };
  }, []);
  return window.location.search;
}
export const useSearchParams = () => {
  const search = useSearch();
  return useMemo(() => new URLSearchParams(search), [search]);
};
export const usePathname = () => window.location.pathname;
export const useRouter = () => ({
  replace: (url: string, _options?: unknown) => {
    window.history.replaceState(null, '', url);
    notify();
  },
  push: (url: string, _options?: unknown) => {
    window.history.pushState(null, '', url);
    notify();
  },
});
