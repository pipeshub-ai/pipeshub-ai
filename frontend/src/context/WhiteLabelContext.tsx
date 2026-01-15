import { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';

import { useAuthContext } from 'src/auth/hooks';
import { getOrgById, getOrgLogo, getOrgIdFromToken } from 'src/sections/accountdetails/utils';

import type { OrganizationData } from 'src/sections/accountdetails/types/organization-data';

// ----------------------------------------------------------------------

// Constants
const DEFAULT_DISPLAY_NAME = 'Pipeshub';
const CACHE_DURATION_MS = 5 * 60 * 1000; // 5 minutes
const BUSINESS_ACCOUNT_TYPES = ['business', 'organization'] as const;

// Types
interface WhiteLabelData {
  logo: string | null;
  displayName: string;
  isWhiteLabeled: boolean;
  loading: boolean;
  orgData: OrganizationData | null;
}

interface WhiteLabelContextValue extends WhiteLabelData {
  refreshWhiteLabel: () => Promise<void>;
}

interface CacheEntry {
  data: WhiteLabelData;
  timestamp: number;
  userId: string;
}

// Context
const WhiteLabelContext = createContext<WhiteLabelContextValue | undefined>(undefined);

// Cache management
class WhiteLabelCache {
  private cache: CacheEntry | null = null;

  get(userId: string): WhiteLabelData | null {
    if (!this.cache) return null;
    
    // Check if cache is for different user
    if (this.cache.userId !== userId) {
      this.clear();
      return null;
    }
    
    // Check if cache is expired
    const isExpired = Date.now() - this.cache.timestamp > CACHE_DURATION_MS;
    if (isExpired) {
      this.clear();
      return null;
    }
    
    return this.cache.data;
  }

  set(data: WhiteLabelData, userId: string): void {
    this.cache = {
      data,
      timestamp: Date.now(),
      userId,
    };
  }

  clear(): void {
    this.cache = null;
  }

  isValid(userId: string): boolean {
    return this.get(userId) !== null;
  }
}

const cache = new WhiteLabelCache();

// Helpers
const getDisplayName = (orgData: OrganizationData | null): string => {
  if (!orgData) return DEFAULT_DISPLAY_NAME;
  return orgData.shortName || orgData.registeredName || DEFAULT_DISPLAY_NAME;
};

const shouldWhiteLabel = (accountType: string | undefined): boolean =>
  BUSINESS_ACCOUNT_TYPES.includes(accountType as any);

const createDefaultData = (loading = false): WhiteLabelData => ({
  logo: null,
  displayName: DEFAULT_DISPLAY_NAME,
  isWhiteLabeled: false,
  loading,
  orgData: null,
});

// ----------------------------------------------------------------------

type Props = {
  children: React.ReactNode;
};

export function WhiteLabelProvider({ children }: Props) {
  const { user, authenticated, loading: authLoading } = useAuthContext();
  const [whiteLabelData, setWhiteLabelData] = useState<WhiteLabelData>(() =>
    createDefaultData(true)
  );

  // Get stable user ID for cache operations
  const userId = useMemo(() => {
    if (!user) return '';
    return user.id || user._id || user.userId || '';
  }, [user]);

  // Fetch white-label data
  const fetchWhiteLabelData = useCallback(async (skipCache = false): Promise<WhiteLabelData> => {
    // Not authenticated - return default
    if (!authenticated || !user) {
      return createDefaultData(false);
    }

    // Individual account - no white-labeling
    if (!shouldWhiteLabel(user.accountType)) {
      const defaultData = createDefaultData(false);
      cache.set(defaultData, userId);
      return defaultData;
    }

    // Check cache if not skipping
    if (!skipCache) {
      const cachedData = cache.get(userId);
      if (cachedData) {
        return cachedData;
      }
    }

    try {
      // Fetch org data and logo in parallel
      const orgId = getOrgIdFromToken();
      const [orgData, logo] = await Promise.all([
        getOrgById(orgId).catch(() => null),
        getOrgLogo(orgId).catch(() => null),
      ]);

      const data: WhiteLabelData = {
        logo,
        displayName: getDisplayName(orgData),
        isWhiteLabeled: true,
        loading: false,
        orgData: orgData || null,
      };

      // Cache the result
      cache.set(data, userId);
      return data;
    } catch (error) {
      console.error('[WhiteLabelContext] Error fetching white-label data:', error);
      return createDefaultData(false);
    }
  }, [authenticated, user, userId]);

  // Load data on mount and when auth state changes
  useEffect(() => {
    let isMounted = true;

    const loadData = async () => {
      // Still loading auth
      if (authLoading) {
        setWhiteLabelData((prev) => ({ ...prev, loading: true }));
        return;
      }

      // Not authenticated
      if (!authenticated || !user) {
        if (isMounted) {
          setWhiteLabelData(createDefaultData(false));
        }
        return;
      }

      // Individual account - no need to fetch
      if (!shouldWhiteLabel(user.accountType)) {
        if (isMounted) {
          setWhiteLabelData(createDefaultData(false));
        }
        return;
      }

      // Fetch white-label data
      setWhiteLabelData((prev) => ({ ...prev, loading: true }));
      const data = await fetchWhiteLabelData();
      if (isMounted) {
        setWhiteLabelData(data);
      }
    };

    loadData();

    return () => {
      isMounted = false;
    };
  }, [authenticated, authLoading, user, fetchWhiteLabelData]);

  // Clear cache on user change
  useEffect(() => {
    if (!user) {
      cache.clear();
    }
  }, [userId, user]);

  // Refresh function for manual updates (e.g., after logo upload)
  const refreshWhiteLabel = useCallback(async () => {
    if (!authenticated || !user || !shouldWhiteLabel(user.accountType)) {
      return;
    }

    try {
      setWhiteLabelData((prev) => ({ ...prev, loading: true }));
      const data = await fetchWhiteLabelData(true); // Skip cache
      setWhiteLabelData(data);
    } catch (error) {
      console.error('[WhiteLabelContext] Error refreshing white-label data:', error);
      setWhiteLabelData((prev) => ({ ...prev, loading: false }));
    }
  }, [authenticated, user, fetchWhiteLabelData]);

  const contextValue = useMemo<WhiteLabelContextValue>(
    () => ({
      ...whiteLabelData,
      refreshWhiteLabel,
    }),
    [whiteLabelData, refreshWhiteLabel]
  );

  return <WhiteLabelContext.Provider value={contextValue}>{children}</WhiteLabelContext.Provider>;
}

// ----------------------------------------------------------------------

export function useWhiteLabel(): WhiteLabelContextValue {
  const context = useContext(WhiteLabelContext);
  if (context === undefined) {
    throw new Error('useWhiteLabel must be used within a WhiteLabelProvider');
  }
  return context;
}

// Export helpers for external use
export { getDisplayName, shouldWhiteLabel };
