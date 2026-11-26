'use client';

import type { ReactNode } from 'react';
import { useFeatureFlagGuard } from '@/lib/hooks/use-feature-flag-guard';
import { selectAgentHarnessOn } from '@/lib/store/feature-flags-store';

/**
 * A harness page renders only once the server says the agent harness is on for the workspace, and an answer that
 * says it is off sends the reader away (ADR-OPS-06): an install with the harness switched off shows none of it and
 * calls none of its routes. It follows the org's harness (ADR-OPS-08), not run admission: the kill switch or an
 * operator stop refuses new runs but keeps the pages, among them the settings an admin lifts a stop from.
 */
export function HarnessOnly({
  children,
  redirectTo = '/workspace/general',
}: {
  children: ReactNode;
  redirectTo?: string;
}) {
  const { ready } = useFeatureFlagGuard(selectAgentHarnessOn, redirectTo);
  return ready ? <>{children}</> : null;
}
