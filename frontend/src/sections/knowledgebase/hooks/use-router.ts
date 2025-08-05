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

  // Function to parse URL parameters
  const parseURLParams = useCallback(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const view = urlParams.get('view') as RouteParams['view'];
    const kbId = urlParams.get('kbId') || undefined;
    const folderId = urlParams.get('folderId') || undefined;

    if (view && ['dashboard', 'knowledge-base', 'folder', 'all-records'].includes(view)) {
      return { view, kbId, folderId } as RouteParams;
    }
    return { view: 'dashboard' } as RouteParams;
  }, []);

  // Function to update route from URL
  const updateRouteFromURL = useCallback(() => {
    const newRoute = parseURLParams();
    setRoute(newRoute);
    setIsInitialized(true);
  }, [parseURLParams]);

  // Initialize route from URL on mount
  useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;
    updateRouteFromURL();
  }, [updateRouteFromURL]);

  // Listen for browser back/forward and URL changes
  useEffect(() => {
    const handlePopState = () => {
      updateRouteFromURL();
    };

    // Listen for popstate (browser back/forward)
    window.addEventListener('popstate', handlePopState);

    // Listen for URL changes (for cases where URL is changed programmatically)
    const originalPushState = window.history.pushState;
    const originalReplaceState = window.history.replaceState;

    window.history.pushState = function (...args) {
      originalPushState.apply(window.history, args);
      // Small delay to ensure URL is updated
      setTimeout(() => {
        updateRouteFromURL();
      }, 0);
    };

    window.history.replaceState = function (...args) {
      originalReplaceState.apply(window.history, args);
      // Small delay to ensure URL is updated
      setTimeout(() => {
        updateRouteFromURL();
      }, 0);
    };

    return () => {
      window.removeEventListener('popstate', handlePopState);
      window.history.pushState = originalPushState;
      window.history.replaceState = originalReplaceState;
    };
  }, [updateRouteFromURL]);

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
