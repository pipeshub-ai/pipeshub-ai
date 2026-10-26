import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import {
  VECTOR_DB_SIZING_GUIDE_URL,
  VectorDbMemoryWarning,
  formatGigabytes,
  shouldWarnAboutVectorDbMemory,
  type VectorDbMemoryReport,
} from '../vector-db-memory';

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  return {
    useTranslation: () => ({
      i18n: { language: 'en-US' },
      t: (key: string, values?: Record<string, string>) => {
        let cur: unknown = locale;
        for (const part of key.split('.')) {
          cur = (cur as Record<string, unknown>)?.[part];
        }
        if (typeof cur !== 'string') return key;
        return cur.replace(/{{(\w+)}}/g, (_m, name: string) => values?.[name] ?? '');
      },
    }),
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

const GIB = 1024 ** 3;

const INSUFFICIENT: VectorDbMemoryReport = {
  status: 'insufficient',
  neededBytes: 11.2 * GIB,
  availableBytes: 4 * GIB,
  availableSource: 'configured',
};

function renderWarning(report: VectorDbMemoryReport | null) {
  return render(
    <Theme>
      <VectorDbMemoryWarning report={report} />
    </Theme>,
  );
}

afterEach(cleanup);

describe('shouldWarnAboutVectorDbMemory', () => {
  it('warns only on the server verdict "insufficient" with both figures', () => {
    expect(shouldWarnAboutVectorDbMemory(INSUFFICIENT)).toBe(true);
    expect(shouldWarnAboutVectorDbMemory({ ...INSUFFICIENT, status: 'ok' })).toBe(false);
    expect(shouldWarnAboutVectorDbMemory({ ...INSUFFICIENT, status: 'unknown' })).toBe(false);
    expect(shouldWarnAboutVectorDbMemory({ status: 'unsupported' })).toBe(false);
    expect(shouldWarnAboutVectorDbMemory({ ...INSUFFICIENT, availableBytes: null })).toBe(false);
    expect(shouldWarnAboutVectorDbMemory(null)).toBe(false);
  });
});

describe('formatGigabytes', () => {
  it('shows one decimal in the viewer locale', () => {
    expect(formatGigabytes(11.2 * GIB, 'en-US')).toBe('11.2');
    expect(formatGigabytes(4 * GIB, 'en-US')).toBe('4.0');
    expect(formatGigabytes(11.2 * GIB, 'de-DE')).toBe('11,2');
  });
});

describe('VectorDbMemoryWarning', () => {
  it('names both figures and links the sizing guide', () => {
    renderWarning(INSUFFICIENT);

    expect(screen.getByRole('alert')).toBeTruthy();
    expect(
      screen.getByText(
        /needs about 11\.2 GB of memory to stay fast; the vector database has about 4\.0 GB, so searches read from disk/,
      ),
    ).toBeTruthy();
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe(VECTOR_DB_SIZING_GUIDE_URL);
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('says "server" when the only figure is the host RAM', () => {
    renderWarning({ ...INSUFFICIENT, availableBytes: 8 * GIB, availableSource: 'host' });
    expect(screen.getByText(/the server running the vector database has only about 8\.0 GB/)).toBeTruthy();
  });

  it('renders nothing when memory is enough or unknown', () => {
    const { container: ok } = renderWarning({ ...INSUFFICIENT, status: 'ok' });
    expect(ok.querySelector('[role="alert"]')).toBeNull();
    cleanup();
    const { container: none } = renderWarning(null);
    expect(none.querySelector('[role="alert"]')).toBeNull();
  });
});
