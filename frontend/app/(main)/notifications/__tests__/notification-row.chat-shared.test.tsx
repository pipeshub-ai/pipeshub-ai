import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18n from 'i18next';
import { I18nextProvider } from 'react-i18next';
import enUS from '@/lib/i18n/locales/en-US.json';
import enIN from '@/lib/i18n/locales/en-IN.json';
import deDE from '@/lib/i18n/locales/de-DE.json';
import esES from '@/lib/i18n/locales/es-ES.json';
import hiIN from '@/lib/i18n/locales/hi-IN.json';
import koKR from '@/lib/i18n/locales/ko-KR.json';
import zhCN from '@/lib/i18n/locales/zh-CN.json';
import zhTW from '@/lib/i18n/locales/zh-TW.json';
import zhSG from '@/lib/i18n/locales/zh-SG.json';
import csCZ from '@/lib/i18n/locales/cs-CZ.json';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href}>{children}</a>,
}));

import { NotificationRow } from '../notification-row';
import type { NotificationListItem } from '../api';

const LOCALES = {
  'en-US': enUS,
  'en-IN': enIN,
  'de-DE': deDE,
  'es-ES': esES,
  'hi-IN': hiIN,
  'ko-KR': koKR,
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  'zh-SG': zhSG,
  'cs-CZ': csCZ,
} as const;

type Locale = keyof typeof LOCALES;

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => vi.stubGlobal('ResizeObserver', NoopResizeObserver));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderIn(locale: Locale, notification: Partial<NotificationListItem>) {
  const instance = i18n.createInstance();
  void instance.init({
    lng: locale,
    fallbackLng: 'en-US',
    resources: Object.fromEntries(Object.entries(LOCALES).map(([l, translation]) => [l, { translation }])),
    interpolation: { escapeValue: false },
    initAsync: false,
  });
  const base: NotificationListItem = {
    _id: 'n1',
    type: 'chatShared',
    title: 'Olivia Owner shared a chat with you',
    message: 'You can now open "Roadmap review".',
    redirectLink: '/chat/?conversationId=c1',
    status: 'unread',
    payload: { conversationId: 'c1', sharerName: 'Olivia Owner', chatTitle: 'Roadmap review' },
  };
  return render(
    <I18nextProvider i18n={instance}>
      <Theme>
        <NotificationRow
          notification={{ ...base, ...notification }}
          onMarkRead={vi.fn()}
          onMarkUnread={vi.fn()}
          onArchive={vi.fn()}
          onUnarchive={vi.fn()}
          onDismiss={vi.fn()}
          markReadLabel="read"
          markUnreadLabel="unread"
          archiveLabel="archive"
          unarchiveLabel="unarchive"
          dismissLabel="dismiss"
        />
      </Theme>
    </I18nextProvider>,
  );
}

const text = (container: HTMLElement) => container.textContent ?? '';

describe('NotificationRow — a chat shared with you, in every language', () => {
  it('shows English from the payload in en-US and en-IN', () => {
    for (const locale of ['en-US', 'en-IN'] as const) {
      const { container, unmount } = renderIn(locale, {});
      expect(text(container)).toContain('Olivia Owner shared a chat with you');
      expect(text(container)).toContain('You can now open "Roadmap review".');
      unmount();
    }
  });

  for (const locale of ['de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-TW', 'zh-SG', 'cs-CZ'] as const) {
    it(`${locale}: the title and body come from the locale, with the sharer and chat filled in`, () => {
      const { container } = renderIn(locale, {});
      const shown = text(container);

      expect(shown).toContain('Olivia Owner');
      expect(shown).toContain('Roadmap review');
      expect(shown).not.toContain('shared a chat with you');
      expect(shown).not.toContain('You can now open');
      expect(shown).not.toContain('notifications.chatShared');
    });
  }

  it('uses the agent wording for an agent chat', () => {
    const { container } = renderIn('es-ES', {
      payload: { conversationId: 'c1', agentKey: 'agent-1', sharerName: 'Olivia Owner', chatTitle: 'Roadmap review' },
    });

    expect(text(container)).toContain('chat de agente');
  });

  it('names the "Shared with me" section in the viewer\'s language when the chat has no title', () => {
    const { container } = renderIn('de-DE', { payload: { conversationId: 'c1', sharerName: 'Olivia Owner' } });

    expect(text(container)).toContain('Mit mir geteilt');
  });

  it('falls back to a neutral sharer name when the payload carries none', () => {
    const { container } = renderIn('de-DE', { payload: { conversationId: 'c1', chatTitle: 'Roadmap review' } });

    expect(text(container)).toContain('Ein Kollege');
  });

  it('keeps the stored text for a row that has no conversation in its payload', () => {
    const { container } = renderIn('de-DE', { payload: {} });

    expect(text(container)).toContain('Olivia Owner shared a chat with you');
  });

  it('leaves every other notification type untouched', () => {
    const { container } = renderIn('de-DE', { type: 'conversation_shared', title: 'Stored title', message: 'Stored message' });

    expect(text(container)).toContain('Stored title');
    expect(text(container)).toContain('Stored message');
  });

  it('has the same keys in every locale', () => {
    for (const [locale, messages] of Object.entries(LOCALES)) {
      const keys = Object.keys((messages as typeof enUS).notifications.chatShared).sort();
      expect(keys, locale).toEqual(['body', 'bodyNoTitle', 'someone', 'title', 'titleAgent']);
    }
  });
});
