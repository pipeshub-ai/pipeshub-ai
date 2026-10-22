import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { defaultNotificationExtension } from '../notification-extension';
import type { NotificationListItem } from '../api';

describe('defaultNotificationExtension', () => {
  it('leaves every row as stored, with no extra action, and does nothing on the socket', () => {
    const n = { _id: 'n1', type: 'connector.sync', title: 'Done', status: 'unread' } as NotificationListItem;
    const { result } = renderHook(() =>
      defaultNotificationExtension.usePanelRows({
        isPanelOpen: true,
        beginRowAction: () => true,
        endRowAction: () => {},
        setError: () => {},
      }),
    );
    expect(result.current.decorate(n)).toBeNull();
    expect(result.current.extraAction(n)).toBeNull();
    renderHook(() => defaultNotificationExtension.useSocketEffects({ isHydrated: true, isAuthenticated: true, accessToken: 't' }));
    expect(defaultNotificationExtension.onSocketConnect()).toBeUndefined();
  });
});
