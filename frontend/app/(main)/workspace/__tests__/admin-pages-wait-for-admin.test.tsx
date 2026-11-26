import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

// Bots and Prompts read admin-only settings. Bots loaded them on mount, before
// knowing who the user was, so a member who opened it by URL got a 403 from the
// server. Prompts (fixed on main in #4808) is held to the same contract here.

// Hoisted with the mocks: the pages are imported with the file (below), so the mocks' state must exist first.
const { user, replace, getSlackBotConfigs, getSystemPrompts } = vi.hoisted(() => ({
  user: { isAdmin: null as boolean | null, isProfileInitialized: false },
  replace: vi.fn(),
  getSlackBotConfigs: vi.fn(async () => []),
  getSystemPrompts: vi.fn(async () => ({
    customSystemPrompt: '', customSystemPromptWebSearch: '', customSystemPromptAgent: '',
  })),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof user) => unknown) => selector(user),
  selectIsAdmin: (s: typeof user) => s.isAdmin,
  selectIsProfileInitialized: (s: typeof user) => s.isProfileInitialized,
}));

vi.mock('../bots/api', () => ({
  BotsApi: { getSlackBotConfigs, getAgents: vi.fn(async () => ({ agents: [], complete: true })) },
}));
vi.mock('../bots/components', () => ({
  BotPageLayout: () => <div>bots</div>,
  BotConfigPanel: () => null,
}));

vi.mock('../prompts/api', () => ({ PromptsApi: { getSystemPrompts } }));

vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));

// Imported with the file, not inside a test: the first dynamic import compiled the Bots page's module graph (about
// 4 s cold) inside the first test's 5 s budget, so that test timed out whenever the host was busy. Mocks above are
// hoisted, so the pages still see them.
import BotsPage from '../bots/page';
import PromptsPage from '../prompts/page';

const pages = [
  { name: 'Bots', Page: BotsPage, fetched: getSlackBotConfigs },
  { name: 'Prompts', Page: PromptsPage, fetched: getSystemPrompts },
];

function renderPage(Page: React.ComponentType) {
  return render(
    <Theme>
      <Page />
    </Theme>,
  );
}

describe('admin-only workspace pages', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  for (const page of pages) {
    describe(page.name, () => {
      it('loads nothing while it is not yet known who the user is', async () => {
        Object.assign(user, { isAdmin: null, isProfileInitialized: false });

        renderPage(page.Page);
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(page.fetched).not.toHaveBeenCalled();
      });

      it('sends a member away without asking the server', async () => {
        Object.assign(user, { isAdmin: false, isProfileInitialized: true });

        const { container } = renderPage(page.Page);
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(replace).toHaveBeenCalledWith('/workspace/general');
        expect(page.fetched).not.toHaveBeenCalled();
        expect(container.textContent).toBe('');
      });

      it('sends the user away when their role could not be loaded', async () => {
        Object.assign(user, { isAdmin: null, isProfileInitialized: true });

        renderPage(page.Page);
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(replace).toHaveBeenCalledWith('/workspace/general');
        expect(page.fetched).not.toHaveBeenCalled();
      });

      it('loads the settings for an admin', async () => {
        Object.assign(user, { isAdmin: true, isProfileInitialized: true });

        renderPage(page.Page);

        await waitFor(() => expect(page.fetched).toHaveBeenCalledTimes(1));
        expect(replace).not.toHaveBeenCalled();
      });
    });
  }
});
