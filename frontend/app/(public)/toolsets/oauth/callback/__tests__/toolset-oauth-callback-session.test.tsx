/**
 * The toolset sign-in popup stays open across a sign-out in another tab. The
 * answer to its code exchange is then dropped, so it reports the failure
 * instead of spinning forever with the opener never told.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const toolsetsApi = vi.hoisted(() => ({ completeToolsetOAuthCallback: vi.fn() }));
const messages = vi.hoisted(() => ({
  postToolsetOAuthErrorToOpener: vi.fn(),
  postToolsetOAuthSuccessToOpener: vi.fn(),
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi }));
vi.mock('@/app/(main)/toolsets/oauth/toolset-oauth-window-messages', () => messages);
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { clearSessionCache } from '@/lib/api/session-cache';
import i18n from '@/lib/i18n/config';
import { ToolsetOAuthCallbackClient } from '../toolset-oauth-callback-client';

beforeEach(() => {
  window.history.replaceState(null, '', '/toolsets/oauth/callback/slack?code=c1&state=s1');
  toolsetsApi.completeToolsetOAuthCallback.mockReturnValue(new Promise(() => {}));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the toolset sign-in popup at session end', () => {
  it('shows the error and tells the opener instead of spinning forever', async () => {
    render(
      <Theme>
        <ToolsetOAuthCallbackClient />
      </Theme>,
    );
    await act(async () => {});
    expect(toolsetsApi.completeToolsetOAuthCallback).toHaveBeenCalledTimes(1);

    act(() => clearSessionCache());

    const hint = i18n.t('agentBuilder.toolsetOAuthErrorRetryHint');
    expect(messages.postToolsetOAuthErrorToOpener).toHaveBeenCalledWith('slack', hint);
    expect(screen.getByText(hint)).toBeTruthy();
    expect(screen.queryByText(i18n.t('agentBuilder.toolsetOAuthCompletingTitle'))).toBeNull();
  });

  it('reports once, and not again at a later session end', async () => {
    render(
      <Theme>
        <ToolsetOAuthCallbackClient />
      </Theme>,
    );
    await act(async () => {});

    act(() => clearSessionCache());
    act(() => clearSessionCache());

    expect(messages.postToolsetOAuthErrorToOpener).toHaveBeenCalledTimes(1);
  });

  it('is not affected by a session end after the exchange finished', async () => {
    toolsetsApi.completeToolsetOAuthCallback.mockResolvedValue({ success: true });
    render(
      <Theme>
        <ToolsetOAuthCallbackClient />
      </Theme>,
    );
    await act(async () => {});

    act(() => clearSessionCache());

    expect(messages.postToolsetOAuthErrorToOpener).not.toHaveBeenCalled();
  });
});
