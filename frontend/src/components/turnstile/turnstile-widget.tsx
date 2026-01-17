/* eslint-disable react/no-unused-prop-types */
import { useEffect, useRef, useState, memo, useImperativeHandle, forwardRef } from 'react';

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

export interface TurnstileWidgetHandle {
  reset: () => void;
}

export const TurnstileWidget = memo(forwardRef<TurnstileWidgetHandle, TurnstileWidgetProps>(({
  siteKey,
  onSuccess,
  onError,
  onExpire,
  theme = 'auto',
  size = 'normal',
  className,
}, ref) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [isReady, setIsReady] = useState(false);
  const isMountedRef = useRef(true);

  // Expose reset method to parent components
  useImperativeHandle(ref, () => ({
    reset: () => {
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.reset(widgetIdRef.current);
        } catch (error) {
          console.error('Error resetting Turnstile widget:', error);
        }
      }
    },
  }));

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // Check if Turnstile script is loaded
    const checkTurnstile = () => {
      if (window.turnstile && isMountedRef.current) {
        setIsReady(true);
      } else if (!isMountedRef.current) {
        setTimeout(checkTurnstile, 100);
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
        console.error('Error rendering Turnstile widget:', error);
      }
    }

    // Cleanup function
    return () => {
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
          widgetIdRef.current = null;
        } catch (error) {
          console.error('Error removing Turnstile widget:', error);
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
}));
