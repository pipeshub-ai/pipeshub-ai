/**
 * Round 12 (WP-52 x WP-51): an agent's "Safety and checks" (plan approval, result checks) do not travel with an
 * export yet (queued for WP-56). The export says so before download, and an import says to set them again.
 *
 * R12-AX-2 the import result keeps `gates` and the builder's pinned list names it to set again.
 * R12-AX-3 (superseded by WP56-GATES-6: they travel now).
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { ImportNeedsChoice } from '../import-needs-choice';
import { toImportResult } from '../../config-transfer';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(cleanup);

describe('agent gates left out of an export', () => {
  it('R12-AX-2: the import names the agent gates to set again', () => {
    const result = toImportResult({ agentKey: 'k', name: 'Triage', excluded: ['gates', 'sharing'] });
    expect(result?.excluded).toEqual(['gates', 'sharing']);
    render(
      <Theme>
        <ImportNeedsChoice result={result!} onChoose={vi.fn()} onClose={vi.fn()} />
      </Theme>,
    );
    expect(screen.getByText(/Set again yourself: .*its Safety and checks/)).toBeTruthy();
  });
});
