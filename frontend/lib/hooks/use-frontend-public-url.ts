import { useEffect, useState } from 'react';
import { fetchFrontendPublicUrl } from '@/lib/api/frontend-public-url';

/** Configured public frontend URL, or null while loading or when it cannot be resolved. */
export function useFrontendPublicUrl(): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    fetchFrontendPublicUrl().then((resolved) => {
      if (mounted) setUrl(resolved);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return url;
}
