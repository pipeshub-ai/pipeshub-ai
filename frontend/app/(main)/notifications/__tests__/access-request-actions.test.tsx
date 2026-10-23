import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
const post = vi.hoisted(() => vi.fn());
const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiClient: { post, get } }));

import { AccessRequestActions } from '../access-request-actions';

beforeEach(() => get.mockResolvedValue({ data: { request: { status: 'pending' } } }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const renderActions = () =>
  render(<Theme><AccessRequestActions requestId="req1" requesterName="Casey" collectionName="Finance" /></Theme>);

describe('AccessRequestActions', () => {
  it('approves through the API and shows the result', async () => {
    post.mockResolvedValue({});
    renderActions();
    fireEvent.click(screen.getByText('notifications.accessRequest.approve'));
    // The dialog names the requester and the whole collection before anything is granted.
    expect(await screen.findByText('notifications.accessRequest.approveBodyCollection')).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByText('notifications.accessRequest.approve').at(-1)!);
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/access-requests/req1/approve', {}, { suppressErrorToast: true }));
    await waitFor(() => expect(screen.getByTestId('access-request-result').textContent).toBe('notifications.accessRequest.approved'));
  });

  it('denies through the API', async () => {
    post.mockResolvedValue({});
    renderActions();
    fireEvent.click(screen.getByText('notifications.accessRequest.deny'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/access-requests/req1/deny', {}, { suppressErrorToast: true }));
    await waitFor(() => expect(screen.getByTestId('access-request-result').textContent).toBe('notifications.accessRequest.denied'));
  });

  it('keeps the buttons and shows an error when the decision fails', async () => {
    post.mockRejectedValue({ message: 'Access could not be granted' });
    renderActions();
    fireEvent.click(screen.getByText('notifications.accessRequest.approve'));
    fireEvent.click((await screen.findAllByText('notifications.accessRequest.approve')).at(-1)!);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Access could not be granted'));
  });

  it('shows an already-decided request as decided, with no buttons', async () => {
    get.mockResolvedValue({ data: { request: { status: 'approved' } } });
    renderActions();
    await waitFor(() => expect(screen.getByTestId('access-request-result').textContent).toBe('notifications.accessRequest.alreadyApproved'));
    expect(screen.queryByTestId('access-request-actions')).toBeNull();
  });

  it('shows a connector record’s request as handled in its source, with no buttons', async () => {
    get.mockResolvedValue({ data: { request: { status: 'forwarded' } } });
    renderActions();
    await waitFor(() => expect(screen.getByTestId('access-request-result').textContent).toBe('notifications.accessRequest.forwarded'));
    expect(screen.queryByTestId('access-request-actions')).toBeNull();
  });

  it('says so when the server reports it was decided meanwhile', async () => {
    post.mockRejectedValue({ message: 'This request was already decided' });
    get.mockResolvedValueOnce({ data: { request: { status: 'pending' } } });
    get.mockResolvedValueOnce({ data: { request: { status: 'denied' } } });
    renderActions();
    fireEvent.click(screen.getByText('notifications.accessRequest.deny'));
    await waitFor(() => expect(screen.getByTestId('access-request-result').textContent).toBe('notifications.accessRequest.alreadyDeclined'));
  });
});
