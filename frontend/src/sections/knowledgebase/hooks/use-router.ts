import { useRef, useState, useEffect, useCallback } from 'react';

export interface RouteParams {
  view: 'dashboard' | 'knowledge-base' | 'folder' | 'all-records';
  kbId?: string;
  folderId?: string;
}

export const useRouter = () => {
  const [route, setRoute] = useState<RouteParams>({ view: 'dashboard' });
  const [isInitialized, setIsInitialized] = useState(false);
  const initRef = useRef(false);

  // Initialize route from URL on mount - ONLY ONCE
  useEffect(() => {
    if (initRef.current) return undefined; // Prevent multiple initializations
    initRef.current = true;

    const initializeFromURL = () => {
      const urlParams = new URLSearchParams(window.location.search);
      const view = urlParams.get('view') as RouteParams['view'];
      const kbId = urlParams.get('kbId') || undefined;
      const folderId = urlParams.get('folderId') || undefined;

      if (view && ['dashboard', 'knowledge-base', 'folder', 'all-records'].includes(view)) {
        setRoute({ view, kbId, folderId });
      } else {
        setRoute({ view: 'dashboard' });
      }
      setIsInitialized(true);
    };

    initializeFromURL();

    // Listen for browser back/forward
    const handlePopState = () => {
      initializeFromURL();
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []); // Empty dependency array

  const navigate = useCallback((newRoute: RouteParams) => {
    setRoute(newRoute);
    
    // Update browser URL
    const searchParams = new URLSearchParams();
    searchParams.set('view', newRoute.view);
    if (newRoute.kbId) searchParams.set('kbId', newRoute.kbId);
    if (newRoute.folderId) searchParams.set('folderId', newRoute.folderId);
    
    const newUrl = `${window.location.pathname}?${searchParams.toString()}`;
    window.history.pushState(null, '', newUrl);
  }, []);

  const navigateBack = useCallback(() => {
    window.history.back();
  }, []);

  return { route, navigate, navigateBack, isInitialized };
};