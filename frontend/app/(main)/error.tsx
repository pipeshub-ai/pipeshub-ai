'use client';

import { ErrorFallback } from '@/app/components/ui/error-boundary';

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return <ErrorFallback error={error} reset={reset} />;
}
