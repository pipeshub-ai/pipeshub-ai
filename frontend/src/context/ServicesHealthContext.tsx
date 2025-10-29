import React, { createContext, useContext, useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { CONFIG } from 'src/config-global';
import { toast } from 'src/components/snackbar';

type HealthState = {
  loading: boolean;
  healthy: boolean | null;
  services: { query: string; connector: string } | null;
};

const ServicesHealthContext = createContext<HealthState | undefined>(undefined);

export function ServicesHealthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState<boolean>(true);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [services, setServices] = useState<HealthState['services']>(null);
  const toastIdRef = useRef<string | number | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Check localStorage for cached health status
  const checkLocalStorageHealth = (): boolean => {
    try {
      const healthCheck = localStorage.getItem('healthCheck');
      return healthCheck === 'true';
    } catch (err) {
      console.error('Failed to read from localStorage:', err);
      return false;
    }
  };

  // Save health status to localStorage
  const saveHealthToLocalStorage = (isHealthy: boolean) => {
    try {
      localStorage.setItem('healthCheck', isHealthy.toString());
    } catch (err) {
      console.error('Failed to save to localStorage:', err);
    }
  };

  // Simple health check function
  const checkHealth = useCallback(async () => {
    try {
      console.log('Checking services health...');
      const resp = await fetch(`${CONFIG.backendUrl}/api/v1/health/services`, { credentials: 'include' });
      const data = await resp.json();
      const ok = resp.ok && data?.status === 'healthy';
      
      setHealthy(ok);
      setServices(data?.services ?? null);
      setLoading(false);

      // Update toast based on result
      if (ok && toastIdRef.current != null) {
        toast.success('Services are healthy', { id: toastIdRef.current });
        toastIdRef.current = null;
        // Save to localStorage when services are healthy
        saveHealthToLocalStorage(true);
        // Stop polling when services are healthy
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
      } else if (!ok) {
        // Keep showing loading toast while polling
        if (toastIdRef.current == null) {
          toastIdRef.current = toast.loading('Waiting for services to become ready...', { duration: Infinity });
        }
      }
    } catch (err) {
      console.error('Health check failed:', err);
      setHealthy(false);
      setServices(null);
      setLoading(false);
      
      // Show error toast and keep polling
      if (toastIdRef.current != null) {
        toast.error('Failed to connect to services. Retrying...', { id: toastIdRef.current });
        toastIdRef.current = null;
      }
    }
  }, []);

  // Show loading toast after a small delay to ensure toaster is mounted
  useEffect(() => {
    // Check if health is already cached
    const cachedHealthy = checkLocalStorageHealth();
    
    if (!cachedHealthy) {
      // Show loading toast only if we need to check health
      const timer = setTimeout(() => {
        toastIdRef.current = toast.loading('Checking services health. Please wait...', { duration: Infinity });
      }, 100);
      
      return () => clearTimeout(timer);
    }

    // If cached, skip health check
    console.log('Health check skipped - using cached value from localStorage');
    setHealthy(true);
    setLoading(false);
    return undefined;
  }, []);

  // Start polling on mount
  useEffect(() => {
    // Check if health is already cached
    const cachedHealthy = checkLocalStorageHealth();
    
    if (!cachedHealthy) {
      // Initial check
      checkHealth();
      
      // Start polling every 5 seconds
      pollIntervalRef.current = setInterval(checkHealth, 5000);
    }
    
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      if (toastIdRef.current != null) {
        toast.dismiss(toastIdRef.current);
        toastIdRef.current = null;
      }
    };
  }, [checkHealth]);

  const value = useMemo<HealthState>(() => ({ loading, healthy, services }), [loading, healthy, services]);

  return <ServicesHealthContext.Provider value={value}>{children}</ServicesHealthContext.Provider>;
}

export function useServicesHealth() {
  const ctx = useContext(ServicesHealthContext);
  if (!ctx) throw new Error('useServicesHealth must be used within ServicesHealthProvider');
  return ctx;
}
