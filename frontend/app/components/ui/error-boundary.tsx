'use client';

import React from 'react';
import Link from 'next/link';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

export function ErrorFallback({
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
