/** BETA: the org's AI settings page (the default coding engine) belongs to the agent harness, so its title is Beta. */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { OrgAiAccess } from '../components/org-ai-access';

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isInitialized: boolean }) => s.isInitialized,
}));
vi.mock('../components/org-coding-engine', () => ({ OrgCodingEngine: () => null }));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

describe('OrgAiAccess', () => {
  it('shows the Beta badge beside the page title', () => {
    render(<Theme><OrgAiAccess /></Theme>);
    const title = screen.getByRole('heading', { level: 1, name: i18n.t('aiAccounts.org.title') });
    expect(title.parentElement?.textContent).toContain(i18n.t('common.beta.label'));
  });
});
