import { useState, useEffect } from 'react';
import axios from 'src/utils/axios';

interface PlatformSettings {
  fileUploadMaxSizeBytes: number;
  featureFlags: Record<string, boolean>;
}

export function usePlatformSettings() {
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await axios.get('/api/v1/configurationManager/platform/settings');
        if (mounted) {
          setSettings({
            fileUploadMaxSizeBytes: res.data?.fileUploadMaxSizeBytes || 30 * 1024 * 1024,
            featureFlags: res.data?.featureFlags || {},
          });
          setError(null);
        }
      } catch (e: any) {
        if (mounted) {
          setError(e?.response?.data?.message || e?.message || 'Failed to load platform settings');
        }
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const isFeatureEnabled = (flagKey: string): boolean =>
    settings?.featureFlags[flagKey] || false;

  return { settings, loading, error, isFeatureEnabled };
}

