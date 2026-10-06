import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { ArtifactsHeader } from '../header';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

afterEach(cleanup);

// The gallery lists only the caller's own artifacts (USER OWNER edge), so no locale may call it "all".
const MY_ARTIFACTS: Record<string, string> = {
  'en-US': 'My artifacts',
  'en-IN': 'My artifacts',
  'de-DE': 'Meine Artefakte',
  'es-ES': 'Mis artefactos',
  'hi-IN': 'मेरे आर्टिफैक्ट',
  'ko-KR': '내 아티팩트',
  'zh-CN': '我的产物',
  'zh-SG': '我的产物',
  'zh-TW': '我的產物',
};

describe('artifacts gallery label', () => {
  it('titles the page "My artifacts"', () => {
    render(
      <Theme>
        <ArtifactsHeader onFind={() => {}} onRefresh={() => {}} viewMode="list" onViewModeChange={() => {}} />
      </Theme>,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'My artifacts' })).toBeTruthy();
  });

  for (const [locale, label] of Object.entries(MY_ARTIFACTS)) {
    it(`${locale}: the nav item and the page title say "${label}"`, () => {
      const file = JSON.parse(readFileSync(resolve(process.cwd(), `lib/i18n/locales/${locale}.json`), 'utf8')) as {
        nav: { allArtifacts: string };
        artifacts: { title: string };
      };
      expect(file.nav.allArtifacts).toBe(label);
      expect(file.artifacts.title).toBe(label);
    });
  }
});
