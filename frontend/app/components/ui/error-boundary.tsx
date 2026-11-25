'use client';

import React, { useEffect } from 'react';
import Link from 'next/link';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** A page's code split that did not arrive (a network change mid-load, or a deploy that replaced the files). */
export function isChunkLoadError(error: Error | null | undefined): boolean {
  if (!error) return false;
  return error.name === 'ChunkLoadError' || /Loading (CSS )?chunk [\w-]+ failed/i.test(error.message ?? '');
}

const CHUNK_RELOAD_KEY = 'pipeshub-chunk-reload-at';
const CHUNK_RELOAD_WINDOW_MS = 30_000;

/**
 * QA-AUTO F2: a code split that failed to load is not a broken page: loading the page again fetches it (React keeps
 * a lazy import's failure, so "Try again" alone throws it again). One reload, then the fallback stays: a second
 * failure within the window is a real outage, and reloading again would loop.
 */
export function reloadOnceForChunk(error: Error, now: number = Date.now(), storage: Storage | undefined = globalThis.sessionStorage, reload: () => void = () => window.location.reload()): boolean {
  if (!isChunkLoadError(error) || !storage) return false;
  const last = Number(storage.getItem(CHUNK_RELOAD_KEY) ?? 0);
  if (Number.isFinite(last) && now - last < CHUNK_RELOAD_WINDOW_MS) return false;
  storage.setItem(CHUNK_RELOAD_KEY, String(now));
  reload();
  return true;
}

export function ErrorFallback({
  error,
  reset,
  compact = false,
  nested = false,
}: {
  error: Error;
  reset: () => void;
  compact?: boolean;
  /** Inside a container that already has a height (shell, canvas): fill it instead of the viewport. */
  nested?: boolean;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    reloadOnceForChunk(error);
  }, [error]);
  if (compact) {
    return (
      <Flex align="center" gap="3" wrap="wrap" role="alert" style={{ padding: 'var(--space-3)' }}>
        <Text size="2" color="gray">{t('common.errorBoundary.body')}</Text>
        <Button size="2" variant="soft" onClick={reset}>{t('common.errorBoundary.retry')}</Button>
      </Flex>
    );
  }
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      role="alert"
      className={nested ? undefined : 'viewport-height'}
      style={{ padding: 'var(--space-5)', textAlign: 'center', ...(nested ? { height: '100%' } : null) }}
    >
      <Heading size="5">{t('common.errorBoundary.title')}</Heading>
      <Text size="2" color="gray">{t('common.errorBoundary.body')}</Text>
      <Flex gap="3">
        <Button size="3" onClick={reset}>{t('common.errorBoundary.retry')}</Button>
        <Button size="3" variant="soft" asChild>
          <Link href="/chat/">{t('common.errorBoundary.goToChat')}</Link>
        </Button>
      </Flex>
    </Flex>
  );
}

interface ErrorBoundaryProps {
  fallback?: (error: Error, reset: () => void) => React.ReactNode;
  resetKeys?: readonly unknown[];
  onError?: (error: Error) => void;
  children: React.ReactNode;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('[ErrorBoundary]', error);
    this.props.onError?.(error);
  }

  componentDidUpdate(prev: ErrorBoundaryProps) {
    const a = prev.resetKeys ?? [];
    const b = this.props.resetKeys ?? [];
    if (this.state.error && (a.length !== b.length || a.some((k, i) => !Object.is(k, b[i])))) {
      this.reset();
    }
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return this.props.fallback
      ? this.props.fallback(error, this.reset)
      : <ErrorFallback error={error} reset={this.reset} />;
  }
}
