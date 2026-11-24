import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { HealthWarnings, readHealthWarnings } from '../health-warnings';

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  return {
    useTranslation: () => ({
      i18n: { language: 'en-US' },
      t: (key: string) => {
        let cur: unknown = locale;
        for (const part of key.split('.')) {
          cur = (cur as Record<string, unknown>)?.[part];
        }
        return typeof cur === 'string' ? cur : key;
      },
    }),
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

const PRECISION_WARNING =
  'Indexing is waiting: the embedding server serves a different weight precision than the search index holds. ' +
  'Set EMBEDDING_SERVER_PRECISION back, or run the vector-store cleanup and then reindex.';

afterEach(cleanup);

function renderWarnings(warnings: string[]) {
  return render(
    <Theme>
      <HealthWarnings warnings={warnings} />
    </Theme>,
  );
}

describe('readHealthWarnings', () => {
  it('keeps the strings the API sent and drops everything else', () => {
    expect(readHealthWarnings([PRECISION_WARNING, '', '  ', 3, null, { a: 1 }])).toEqual([PRECISION_WARNING]);
  });

  it('reads a missing or malformed field as no warnings', () => {
    expect(readHealthWarnings(undefined)).toEqual([]);
    expect(readHealthWarnings('a string')).toEqual([]);
    expect(readHealthWarnings({ length: 1 })).toEqual([]);
  });

  it('shows at most five', () => {
    expect(readHealthWarnings(Array.from({ length: 9 }, (_, i) => `w${i}`))).toHaveLength(5);
  });
});

describe('HealthWarnings', () => {
  it('shows the precision warning with both remedies', () => {
    renderWarnings([PRECISION_WARNING]);
    const alert = screen.getByRole('status');
    expect(alert.textContent).toContain('Indexing is waiting');
    expect(alert.textContent).toContain('EMBEDDING_SERVER_PRECISION');
    expect(alert.textContent).toContain('vector-store cleanup');
    expect(screen.getByText('Attention needed')).toBeTruthy();
  });

  it('is announced politely, not as an alert', () => {
    renderWarnings([PRECISION_WARNING]);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('health-warnings').getAttribute('role')).toBe('status');
  });

  it('shows nothing when the API sent no warning', () => {
    renderWarnings([]);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('the warning title in every locale', () => {
  const english = new Set(['en-US', 'en-IN']);
  const locales = ['cs-CZ', 'de-DE', 'en-IN', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

  it.each(locales)('%s has a title', async (name) => {
    const { default: base } = await import('@/lib/i18n/locales/en-US.json');
    const { default: locale } = await import(`@/lib/i18n/locales/${name}.json`);
    const title = locale.workspace.services.healthWarnings.title as string;
    expect(title.trim().length).toBeGreaterThan(0);
    if (!english.has(name)) {
      expect(title).not.toBe(base.workspace.services.healthWarnings.title);
    }
  });
});
