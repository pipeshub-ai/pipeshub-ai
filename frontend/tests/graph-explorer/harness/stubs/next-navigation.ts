import { useEffect, useMemo, useState } from 'react';

// Re-renders on Back and Forward, as the real router does.
function useSearch(): string {
  const [, bump] = useState(0);
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, []);
  return window.location.search;
}
export const useSearchParams = () => {
  const search = useSearch();
  return useMemo(() => new URLSearchParams(search), [search]);
};
export const usePathname = () => window.location.pathname;
export const useRouter = () => ({
  replace: (url: string, _options?: unknown) => window.history.replaceState(null, '', url),
  push: (url: string, _options?: unknown) => window.history.pushState(null, '', url),
});
