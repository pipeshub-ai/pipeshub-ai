import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { CONFIG } from 'src/config-global';
import { toast } from 'src/components/snackbar';

type HealthState = {
  loading: boolean;
  healthy: boolean | null;
  services: { query: string; connector: string } | null;
  refresh: () => Promise<void>;
};

const ServicesHealthContext = createContext<HealthState | undefined>(undefined);

export function ServicesHealthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState<boolean>(true);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [services, setServices] = useState<HealthState['services']>(null);
  const fetchedRef = useRef<boolean>(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const latestHealthyRef = useRef<boolean | null>(null);
  const toastIdRef = useRef<string | number | null>(null);

  const fetchHealth = async () => {
    try {
      const resp = await fetch(`${CONFIG.backendUrl}/api/v1/health/services`, { credentials: 'include' });
      const data = await resp.json();
      const ok = resp.ok && data?.status === 'healthy';
      setHealthy(ok);
      latestHealthyRef.current = ok;
      setServices(data?.services ?? null);

      if (!ok) {
        if (toastIdRef.current == null) {
          toastIdRef.current = toast.loading('Initializing services. Please wait...', { duration: Infinity });
        }
      } else if (ok && toastIdRef.current != null) {
        toast.success('Services are ready', { id: toastIdRef.current });
        toastIdRef.current = null;
      }
    } catch (err) {
      setHealthy(false);
      latestHealthyRef.current = false;
      setServices(null);

      if (toastIdRef.current == null) {
        toastIdRef.current = toast.loading('Initializing services. Please wait...', { duration: Infinity });
      }
    } finally {
      // Keep loading until we are healthy
      // ok may be undefined here if an exception occurred; treat as not ok
      // To avoid TS complaint, recompute from latestHealthyRef
      setLoading(!(latestHealthyRef.current === true));
    }
  };

  useEffect(() => {
    if (fetchedRef.current) {
      return () => {};
    }
    fetchedRef.current = true;
    const startPolling = async () => {
      await fetchHealth();
      // Continue polling until healthy === true
      if (latestHealthyRef.current !== true) {
        pollRef.current = setInterval(async () => {
          await fetchHealth();
          if (latestHealthyRef.current === true && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }, 3000);
      }
    };
    startPolling();
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      if (toastIdRef.current != null) {
        toast.dismiss(toastIdRef.current);
        toastIdRef.current = null;
      }
    };
  }, []);

  const value = useMemo<HealthState>(() => ({ loading, healthy, services, refresh: fetchHealth }), [loading, healthy, services]);

  // Expose health status globally for non-React consumers (e.g., axios interceptors)
  useEffect(() => {
    (window as any).__servicesHealth = { loading, healthy };
    return () => { delete (window as any).__servicesHealth; };
  }, [loading, healthy]);

  return <ServicesHealthContext.Provider value={value}>{children}</ServicesHealthContext.Provider>;
}

export function useServicesHealth() {
  const ctx = useContext(ServicesHealthContext);
  if (!ctx) throw new Error('useServicesHealth must be used within ServicesHealthProvider');
  return ctx;
}
