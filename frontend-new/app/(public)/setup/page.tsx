'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Box, Text, Heading } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { setApiBaseUrl, getApiBaseUrl } from '@/lib/api/base-url';

type Status = 'idle' | 'validating' | 'success' | 'error';

export default function SetupPage() {
  const router = useRouter();
  const [url, setUrl] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const existing = getApiBaseUrl();
    if (existing) setUrl(existing);
    inputRef.current?.focus();
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = url.trim().replace(/\/+$/, '');
      if (!trimmed) return;

      try {
        new URL(trimmed);
      } catch {
        setStatus('error');
        setErrorMsg('Please enter a valid URL (e.g. https://pipeshub.mycompany.com)');
        return;
      }

      setStatus('validating');
      setErrorMsg('');

      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);

        const response = await fetch(`${trimmed}/api/v1/health`, {
          method: 'GET',
          signal: controller.signal,
        });
        clearTimeout(timeout);

        if (!response.ok) {
          throw new Error(`Server responded with HTTP ${response.status}`);
        }

        setApiBaseUrl(trimmed);
        setStatus('success');

        setTimeout(() => router.push('/login'), 600);
      } catch (err) {
        console.error('Setup health check failed:', err);
        setStatus('error');
        if (err instanceof DOMException && err.name === 'AbortError') {
          setErrorMsg('Connection timed out. Please check the URL and try again.');
        } else if (err instanceof TypeError && (err.message.includes('fetch') || err.message.includes('network') || err.message.includes('CORS'))) {
          setErrorMsg(
            `Network error — this is usually a CORS issue. Ensure the server allows origin "http://tauri.localhost" in its CORS configuration.`,
          );
        } else {
          const detail = err instanceof Error ? err.message : '';
          setErrorMsg(
            `Could not reach the PipesHub server.${detail ? ` (${detail})` : ''} Verify the URL and ensure the server is running.`,
          );
        }
      }
    },
    [url, router],
  );

  return (
    <Flex
      align="center"
      justify="center"
      style={{ minHeight: '100dvh', padding: 'var(--space-5)' }}
    >
      <Box style={{ width: '100%', maxWidth: 480 }}>
        <Flex direction="column" align="center" gap="2" style={{ marginBottom: 'var(--space-6)' }}>
          <Box
            style={{
              width: 56,
              height: 56,
              borderRadius: 'var(--radius-3)',
              backgroundColor: 'var(--emerald-3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <MaterialIcon name="dns" size={28} color="var(--emerald-9)" />
          </Box>
          <Heading size="5" weight="bold" align="center">
            Connect to PipesHub
          </Heading>
          <Text size="2" color="gray" align="center" style={{ maxWidth: 360 }}>
            Enter the URL of your self-hosted PipesHub deployment to get started.
          </Text>
        </Flex>

        <form onSubmit={handleSubmit}>
          <Flex direction="column" gap="3">
            <Box>
              <Text
                as="label"
                size="2"
                weight="medium"
                style={{ display: 'block', marginBottom: 'var(--space-1)' }}
              >
                Server URL
              </Text>
              <input
                ref={inputRef}
                type="url"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (status === 'error') {
                    setStatus('idle');
                    setErrorMsg('');
                  }
                }}
                placeholder="https://pipeshub.mycompany.com"
                autoComplete="url"
                spellCheck={false}
                style={{
                  width: '100%',
                  height: 44,
                  padding: '0 var(--space-3)',
                  fontSize: 14,
                  fontFamily: 'inherit',
                  border:
                    status === 'error'
                      ? '1.5px solid var(--red-8)'
                      : '1px solid var(--gray-6)',
                  borderRadius: 'var(--radius-2)',
                  backgroundColor: 'var(--color-background)',
                  color: 'var(--gray-12)',
                  outline: 'none',
                  transition: 'border-color 150ms',
                  boxSizing: 'border-box',
                }}
                onFocus={(e) => {
                  if (status !== 'error') {
                    e.currentTarget.style.borderColor = 'var(--emerald-8)';
                  }
                }}
                onBlur={(e) => {
                  if (status !== 'error') {
                    e.currentTarget.style.borderColor = 'var(--gray-6)';
                  }
                }}
              />
              {status === 'error' && errorMsg && (
                <Text size="1" color="red" style={{ display: 'block', marginTop: 'var(--space-1)' }}>
                  {errorMsg}
                </Text>
              )}
            </Box>

            <button
              type="submit"
              disabled={status === 'validating' || status === 'success' || !url.trim()}
              style={{
                height: 44,
                borderRadius: 'var(--radius-2)',
                border: 'none',
                backgroundColor:
                  status === 'success' ? 'var(--emerald-9)' : 'var(--emerald-9)',
                color: '#fff',
                fontSize: 14,
                fontWeight: 600,
                fontFamily: 'inherit',
                cursor:
                  status === 'validating' || status === 'success' || !url.trim()
                    ? 'not-allowed'
                    : 'pointer',
                opacity:
                  status === 'validating' || !url.trim() ? 0.7 : 1,
                transition: 'opacity 150ms, background-color 150ms',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 'var(--space-2)',
              }}
            >
              {status === 'validating' && (
                <MaterialIcon name="sync" size={18} color="#fff" style={{ animation: 'spin 1s linear infinite' }} />
              )}
              {status === 'success' && <MaterialIcon name="check_circle" size={18} color="#fff" />}
              {status === 'validating'
                ? 'Connecting...'
                : status === 'success'
                  ? 'Connected'
                  : 'Connect'}
            </button>
          </Flex>
        </form>

        {getApiBaseUrl() && status !== 'success' && (
          <Flex justify="center" style={{ marginTop: 'var(--space-4)' }}>
            <button
              type="button"
              onClick={() => router.push('/login')}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--emerald-9)',
                fontSize: 13,
                fontFamily: 'inherit',
                cursor: 'pointer',
                textDecoration: 'underline',
                textUnderlineOffset: 2,
              }}
            >
              Skip — use current server
            </button>
          </Flex>
        )}
      </Box>
    </Flex>
  );
}
