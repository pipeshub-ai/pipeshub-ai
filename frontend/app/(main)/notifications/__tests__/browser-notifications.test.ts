import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { NotificationListItem } from '../api';
import {
  shouldShowDesktopNotification,
  showDesktopNotification,
} from '../browser-notifications';
import { useBrowserNotificationsStore } from '../browser-notifications-store';

function makeItem(
  overrides: Partial<NotificationListItem> = {},
): NotificationListItem {
  return {
    _id: 'n-1',
    type: 'connector.sync',
    title: 'Sync complete',
    message: 'Your connector finished syncing.',
    status: 'unread',
    ...overrides,
  };
}

describe('showDesktopNotification', () => {
  const notificationCtor = vi.fn();

  beforeEach(() => {
    useBrowserNotificationsStore.setState({
      desktopEnabled: true,
      promptDecision: 'ask',
      snoozedUntil: null,
    });

    notificationCtor.mockClear();
    notificationCtor.mockImplementation(() => ({
      close: vi.fn(),
      onclick: null,
    }));

    vi.stubGlobal('Notification', notificationCtor);
    Object.defineProperty(Notification, 'permission', {
      configurable: true,
      value: 'granted',
    });

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows a notification when tab is hidden and desktop is enabled', () => {
    showDesktopNotification(makeItem());

    expect(notificationCtor).toHaveBeenCalledWith('Sync complete', {
      body: 'Your connector finished syncing.',
      tag: 'n-1',
      icon: '/icon.svg',
    });
  });

  it('does not show when tab is visible', () => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });

    showDesktopNotification(makeItem());

    expect(notificationCtor).not.toHaveBeenCalled();
  });

  it('does not show when desktop notifications are disabled', () => {
    useBrowserNotificationsStore.getState().setDesktopEnabled(false);

    showDesktopNotification(makeItem());

    expect(notificationCtor).not.toHaveBeenCalled();
  });

  it('does not show archived or deleted items', () => {
    showDesktopNotification(makeItem({ status: 'archived' }));
    showDesktopNotification(makeItem({ isDeleted: true }));

    expect(notificationCtor).not.toHaveBeenCalled();
  });
});

describe('shouldShowDesktopNotification', () => {
  beforeEach(() => {
    useBrowserNotificationsStore.setState({ desktopEnabled: true });
    vi.stubGlobal('Notification', vi.fn());
    Object.defineProperty(Notification, 'permission', {
      configurable: true,
      value: 'granted',
    });
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns true only when hidden, granted, and enabled', () => {
    expect(shouldShowDesktopNotification()).toBe(true);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    expect(shouldShowDesktopNotification()).toBe(false);
  });
});
