import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const toggleConnector = vi.fn();
const getConnectorInstance = vi.fn();
vi.mock('../../api', () => ({
  ConnectorsApi: {
    toggleConnector: (...args: unknown[]) => toggleConnector(...args),
    getConnectorInstance: (...args: unknown[]) => getConnectorInstance(...args),
  },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { DisableFirstDialog } from '../disable-first-dialog';
import { useConnectorsStore } from '../../store';
import {
  installDomShims,
  renderInTheme,
  makeInstance,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

function renderDialog(
  props: {
    connectorName?: string;
    disabledNoticeSource?: string;
    onProceed?: () => void | Promise<void>;
  } = {}
) {
  const onOpenChange = vi.fn();
  const onProceed = vi.fn(props.onProceed ?? (() => {}));
  renderInTheme(
    <DisableFirstDialog
      open
      onOpenChange={onOpenChange}
      connectorId="conn-1"
      connectorName={'connectorName' in props ? props.connectorName : 'Jira (Engineering)'}
      actionLabel="remove this connector instance"
      disabledNoticeSource={props.disabledNoticeSource}
      onProceed={onProceed}
    />
  );
  return { onOpenChange, onProceed };
}

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  toggleConnector.mockReset().mockResolvedValue({});
  getConnectorInstance.mockReset().mockResolvedValue(makeInstance({ isActive: true }));
});
afterEach(() => cleanup());

describe('DisableFirstDialog', () => {
  it('explains that the connector will be disabled first', () => {
    renderDialog();
    expect(screen.getByText('Connector is currently enabled')).toBeTruthy();
    expect(
      screen.getByText(
        '"Jira (Engineering)" is currently enabled. To remove this connector instance, it will be automatically disabled first. Do you want to proceed?'
      )
    ).toBeTruthy();
  });

  it('falls back to a generic sentence without a name', () => {
    renderDialog({ connectorName: undefined });
    expect(
      screen.getByText(
        'This connector is currently enabled. To remove this connector instance, it will be automatically disabled first. Do you want to proceed?'
      )
    ).toBeTruthy();
  });

  it('says the records stay searchable when the connector stays disabled', () => {
    renderDialog({ disabledNoticeSource: 'Jira' });
    expect(screen.getByText("Records from this connector stay searchable, with the access they had when you disabled it. Changes to sharing in Jira won't reach PipesHub until you enable it again. To remove the records, delete the connector.")).toBeTruthy();
  });

  it('leaves the searchable-records notice out unless asked for', () => {
    renderDialog();
    expect(screen.queryByText(/stay searchable/)).toBeNull();
  });

  it('does nothing to the connector when the user cancels', () => {
    const { onOpenChange, onProceed } = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toggleConnector).not.toHaveBeenCalled();
    expect(onProceed).not.toHaveBeenCalled();
  });

  it('disables sync, marks the instance disabled everywhere, then runs the action', async () => {
    const instance = makeInstance({ isActive: true });
    useConnectorsStore.setState({
      panelConnector: instance,
      instances: [instance],
      activeConnectors: [instance],
      selectedInstance: instance,
    });
    const { onOpenChange, onProceed } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(onProceed).toHaveBeenCalledTimes(1));
    expect(toggleConnector).toHaveBeenCalledWith('conn-1', 'sync', false);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    const s = useConnectorsStore.getState();
    expect(s.panelConnector?.isActive).toBe(false);
    expect(s.instances[0].isActive).toBe(false);
    expect(s.activeConnectors[0].isActive).toBe(false);
    expect(s.selectedInstance?.isActive).toBe(false);
  });

  it('shows progress and blocks cancel while disabling', async () => {
    let finish: () => void = () => {};
    toggleConnector.mockReturnValue(new Promise<void>((r) => (finish = r)));
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    expect(await screen.findByText('Disabling...')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel' }).hasAttribute('disabled')).toBe(true);
    finish();
  });

  it('keeps the dialog open and skips the action when disabling fails', async () => {
    toggleConnector.mockRejectedValue(new Error('You appear to be offline. Check your connection and try again.'));
    const { onOpenChange, onProceed } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Failed to disable connector',
      description: 'You appear to be offline. Check your connection and try again.',
    });
    expect(onProceed).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByRole('button', { name: 'Disable & proceed' }).hasAttribute('disabled')).toBe(false);
  });

  it('marks the instance disabled when a disable that timed out landed anyway', async () => {
    const instance = makeInstance({ isActive: true });
    useConnectorsStore.setState({ panelConnector: instance, instances: [instance], activeConnectors: [instance] });
    toggleConnector.mockRejectedValue({ type: 'SERVER_ERROR', message: 'This took longer than expected to respond.', statusCode: 504 });
    getConnectorInstance.mockResolvedValue({ ...instance, isActive: false });
    const { onProceed } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(useConnectorsStore.getState().instances[0].isActive).toBe(false));
    expect(getConnectorInstance).toHaveBeenCalledWith('conn-1');
    expect(useConnectorsStore.getState().panelConnector?.isActive).toBe(false);
    expect(onProceed).not.toHaveBeenCalled();
  });

  it('keeps the dialog busy until the read after a failed disable returns', async () => {
    let answer: (v: unknown) => void = () => {};
    toggleConnector.mockRejectedValue({ type: 'SERVER_ERROR', message: 'slow', statusCode: 504 });
    getConnectorInstance.mockReturnValue(new Promise((r) => (answer = r)));
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(getConnectorInstance).toHaveBeenCalledWith('conn-1'));
    expect(screen.getByRole('button', { name: 'Cancel' }).hasAttribute('disabled')).toBe(true);
    answer(makeInstance({ isActive: true }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' }).hasAttribute('disabled')).toBe(false));
  });

  it('leaves the instance as it is when the failed disable did not land', async () => {
    const instance = makeInstance({ isActive: true });
    useConnectorsStore.setState({ instances: [instance] });
    toggleConnector.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down', statusCode: 504 });
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(getConnectorInstance).toHaveBeenCalledWith('conn-1'));
    expect(useConnectorsStore.getState().instances[0].isActive).toBe(true);
  });

  it('leaves a server failure to the app-wide error toast instead of adding a second one', async () => {
    // The axios interceptor has already shown this failure, with the server's own words.
    toggleConnector.mockRejectedValue({
      type: 'SERVER_ERROR',
      message: 'The connector service is unavailable.',
      statusCode: 503,
    });
    const { onProceed } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(toggleConnector).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Disable & proceed' }).hasAttribute('disabled')).toBe(false)
    );
    expect(toasts()).toEqual([]);
    expect(onProceed).not.toHaveBeenCalled();
  });

  it('shows the desktop refusal as the named-device info toast, not the raw server text', async () => {
    // Shape and wording Node sends (tokens_manager/utils/connector.utils.ts). The message embeds
    // the connector id, and the interceptor exempts these refusals, so this dialog is the only message.
    const connectorId = '3f6c2a9e-8b41-4d7a-9c15-2e7f0b8d4a61';
    toggleConnector.mockRejectedValue({
      type: 'CONFLICT',
      statusCode: 409,
      message: `Device "Build Mac" that owns connector ${connectorId} is not connected. Open the Pipeshub desktop app on that machine.`,
      details: { code: 'DESKTOP_OFFLINE', connectorId, ownerDeviceName: 'Build Mac' },
    });
    const { onProceed } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'info',
      title:
        'Build Mac, the device that owns this folder, is not connected. Open the PipesHub desktop app on that device to sync.',
    });
    expect(JSON.stringify(toasts()[0])).not.toContain(connectorId);
    expect(onProceed).not.toHaveBeenCalled();
  });

  it('does not report a failure of the follow-up action itself', async () => {
    const { onProceed } = renderDialog({
      onProceed: () => Promise.reject(new Error('save failed')),
    });

    fireEvent.click(screen.getByRole('button', { name: 'Disable & proceed' }));

    await waitFor(() => expect(onProceed).toHaveBeenCalled());
    expect(toasts()).toEqual([]);
  });
});
