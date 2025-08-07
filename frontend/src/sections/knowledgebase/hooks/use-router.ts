import { useRef, useState, useEffect, useCallback } from 'react';

export interface RouteParams {
  view: 'dashboard' | 'knowledge-base' | 'folder' | 'all-records';
  kbId?: string;
  folderId?: string;
}

// Custom event for programmatic navigation
const LOCATION_CHANGE_EVENT = 'locationchange';

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

  // Function to update route from URL - single source of truth
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

  // Listen for navigation events
  useEffect(() => {
    const handleLocationChange = () => {
      updateRouteFromURL();
    };

    // Listen for browser back/forward navigation
    window.addEventListener('popstate', handleLocationChange);
    
    // Listen for custom programmatic navigation events
    window.addEventListener(LOCATION_CHANGE_EVENT, handleLocationChange);

    return () => {
      window.removeEventListener('popstate', handleLocationChange);
      window.removeEventListener(LOCATION_CHANGE_EVENT, handleLocationChange);
    };
  }, [updateRouteFromURL]);

  // Navigate function - URL is single source of truth
  const navigate = useCallback((newRoute: RouteParams) => {
    // Build new URL with updated query parameters
    const searchParams = new URLSearchParams();
    searchParams.set('view', newRoute.view);
    if (newRoute.kbId) searchParams.set('kbId', newRoute.kbId);
    if (newRoute.folderId) searchParams.set('folderId', newRoute.folderId);

    const newUrl = `${window.location.pathname}?${searchParams.toString()}`;
    
    // Update browser URL without triggering page reload
    window.history.pushState(null, '', newUrl);
    
    // Dispatch custom event to notify our hook of the programmatic change
    window.dispatchEvent(new Event(LOCATION_CHANGE_EVENT));
  }, []);

  const navigateBack = useCallback(() => {
    window.history.back();
  }, []);

  return { route, navigate, navigateBack, isInitialized };
};