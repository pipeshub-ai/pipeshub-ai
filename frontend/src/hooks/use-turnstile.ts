import { useState, useCallback } from 'react';

interface UseTurnstileResult {
  turnstileToken: string | null;
  handleSuccess: (token: string) => void;
  handleError: () => void;
  handleExpire: () => void;
  resetTurnstile: () => void;
}

export function useTurnstile(): UseTurnstileResult {
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  const handleSuccess = useCallback((token: string) => {
    setTurnstileToken(token);
  }, []);

  const handleError = useCallback(() => {
    console.error('Turnstile error');
    setTurnstileToken(null);
  }, []);

  const handleExpire = useCallback(() => {
    console.warn('Turnstile token expired');
    setTurnstileToken(null);
  }, []);

  const resetTurnstile = useCallback(() => {
    setTurnstileToken(null);
  }, []);

  return {
    turnstileToken,
    handleSuccess,
    handleError,
    handleExpire,
    resetTurnstile,
  };
}
