import { useEffect, useRef, useState, memo } from 'react';

import Box from '@mui/material/Box';

// Extend Window interface to include turnstile
declare global {
  interface Window {
    turnstile?: {
      render: (
        element: HTMLElement | string,
        options: {
          sitekey: string;
          callback?: (token: string) => void;
          'error-callback'?: () => void;
          'expired-callback'?: () => void;
          theme?: 'light' | 'dark' | 'auto';
          size?: 'normal' | 'compact' | 'flexible';
        }
      ) => string;
      reset: (widgetId?: string) => void;
      remove: (widgetId?: string) => void;
    };
  }
}

interface TurnstileWidgetProps {
  siteKey: string;
  onSuccess: (token: string) => void;
  onError?: () => void;
  onExpire?: () => void;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'flexible';
  className?: string;
}

export const TurnstileWidget = memo(({
  siteKey,
  onSuccess,
  onError,
  onExpire,
  theme = 'auto',
  size = 'normal',
  className,
}: TurnstileWidgetProps) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [isReady, setIsReady] = useState(false);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // Check if Turnstile script is loaded
    const checkTurnstile = () => {
      if (window.turnstile) {
        if (isMountedRef.current) {
          setIsReady(true);
        }
      } else {
        // Retry after a short delay
        if (isMountedRef.current) {
          setTimeout(checkTurnstile, 100);
        }
      }
    };

    checkTurnstile();
  }, []);

  useEffect(() => {
    // Skip if already rendered
    if (widgetIdRef.current) {
      return undefined;
    }

    if (!isReady || !containerRef.current || !siteKey) {
      return undefined;
    }

    // Render Turnstile widget
    if (window.turnstile && containerRef.current) {
      try {
        const widgetId = window.turnstile.render(containerRef.current, {
          sitekey: siteKey,
          callback: (token) => {
            onSuccess(token);
          },
          'error-callback': () => {
            if (onError) onError();
          },
          'expired-callback': () => {
            if (onExpire) onExpire();
          },
          theme,
          size,
        });
        widgetIdRef.current = widgetId;
      } catch (error) {
      }
    }

    // Cleanup function
    return () => {
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
          widgetIdRef.current = null;
        } catch (error) {
          
        }
      }
    };
  }, [isReady, siteKey, onSuccess, onError, onExpire, theme, size]);

  return (
    <Box
      ref={containerRef}
      className={className}
      sx={{
        display: 'flex',
        justifyContent: 'center',
        minHeight: size === 'compact' ? 50 : 65,
      }}
    />
  );
});
