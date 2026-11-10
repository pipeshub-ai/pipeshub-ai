import { describe, expect, it, vi } from 'vitest';
import { CONNECTOR_OAUTH_POST_MESSAGE, listenForConnectorOAuthSuccess } from '../connector-oauth-window-messages';

function post(type: string, origin = window.location.origin) {
  window.dispatchEvent(new MessageEvent('message', { data: { type }, origin }));
}

describe('listenForConnectorOAuthSuccess', () => {
  it('reports a success message from this origin', () => {
    const onSuccess = vi.fn();
    listenForConnectorOAuthSuccess(onSuccess);

    post(CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS);

    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it('ignores another origin and other message types', () => {
    const onSuccess = vi.fn();
    listenForConnectorOAuthSuccess(onSuccess);

    post(CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS, 'https://evil.test');
    post(CONNECTOR_OAUTH_POST_MESSAGE.ERROR);

    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('replaces the listener when registered again, as a hot reload does', () => {
    const first = vi.fn();
    const second = vi.fn();
    listenForConnectorOAuthSuccess(first);
    listenForConnectorOAuthSuccess(second);

    post(CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
