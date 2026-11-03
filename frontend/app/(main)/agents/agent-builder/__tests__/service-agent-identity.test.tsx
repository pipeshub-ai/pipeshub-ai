/**
 * Choosing the identity a service agent searches knowledge as.
 *
 * A service agent is shared with the whole organisation, so this control
 * decides what everyone's questions can reach. These cover what it offers,
 * what it refuses to offer, and what the save actually sends — the last one
 * because the field was silently dropped before it ever reached the server
 * once already.
 */
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { afterEach, describe, expect, it, vi } from 'vitest';

const list = vi.fn();

vi.mock('../../../workspace/service-accounts/api', () => ({
  ServiceAccountsApi: { list: (...a: unknown[]) => list(...a) },
}));

const { ServiceAgentIdentityControl } = await import(
  '../components/service-agent-identity-control'
);
const { extractAgentConfigFromFlow } = await import('../extract-agent-config');

/** jsdom has no ResizeObserver, and the Radix select's scroll area wants one. */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', NoopResizeObserver);

const LIVE = {
  id: 'sa-live',
  slug: 'nightly',
  fullName: 'Nightly sync',
  email: 'svc-nightly@service.pipeshub.internal',
  isDisabled: false,
};
const DISABLED = {
  id: 'sa-off',
  slug: 'retired',
  fullName: 'Retired job',
  email: 'svc-retired@service.pipeshub.internal',
  isDisabled: true,
};

const renderControl = (props: Partial<React.ComponentProps<typeof ServiceAgentIdentityControl>> = {}) =>
  render(
    <Theme>
      <ServiceAgentIdentityControl value={null} onChange={vi.fn()} {...props} />
    </Theme>
  );

afterEach(() => {
  cleanup();
  list.mockReset();
});

describe('the identity control', () => {
  it('says it searches as its creator when nothing is bound', () => {
    renderControl({ value: null });
    expect(
      screen.getByText('Searches as its creator')
    ).toBeTruthy();
  });

  it('names the bound account rather than its id', () => {
    renderControl({ value: 'sa-live', boundLabel: 'Nightly sync' });
    expect(
      screen.getByText('Searches as Nightly sync')
    ).toBeTruthy();
  });

  it('names the bound account without being opened', async () => {
    // The closed button is where an administrator reads what the agent does.
    // Loading only on open left it saying "a service account", which is the
    // one thing they already knew.
    list.mockResolvedValue({ serviceAccounts: [LIVE] });
    renderControl({ value: 'sa-live' });

    await waitFor(() => expect(list).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/Searches as Nightly sync/)).toBeTruthy()
    );
  });

  it('still names a bound account that has since been disabled', async () => {
    // It is filtered out of the choices, but going vague about an agent that
    // is bound right now would hide the thing worth noticing.
    list.mockResolvedValue({ serviceAccounts: [{ ...DISABLED, id: 'sa-live' }] });
    renderControl({ value: 'sa-live' });

    await waitFor(() =>
      expect(screen.getByText(/Searches as Retired job/)).toBeTruthy()
    );
  });

  it('does not offer a disabled service account', async () => {
    list.mockResolvedValue({ serviceAccounts: [LIVE, DISABLED] });
    renderControl();

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(list).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByText('Loading service accounts…')).toBeNull()
    );

    expect(screen.queryByText(/Retired job/)).toBeNull();
  });

  it('shows the bound account as the current choice, so it can be changed', async () => {
    // The dropdown itself is a Radix portal that jsdom will not open, so this
    // asserts what the dialog presents rather than driving the list. Clearing
    // a binding is covered end to end by the payload tests below.
    list.mockResolvedValue({ serviceAccounts: [LIVE] });
    renderControl({ value: 'sa-live', boundLabel: 'Nightly sync' });

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(screen.queryByText('Loading service accounts…')).toBeNull()
    );

    // A bound agent still gets the chooser, which is how a binding is changed
    // or cleared. (The trigger's own label is asserted above.)
    expect(screen.getByRole('combobox')).toBeTruthy();
  });

  it('tells an administrator when there are no service accounts to choose', async () => {
    list.mockResolvedValue({ serviceAccounts: [DISABLED] });
    renderControl();

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(
        screen.getByText(
          'No service accounts yet. Create one in Workspace → Service accounts.'
        )
      ).toBeTruthy()
    );
  });

  it('clears the binding when the creator is the current choice', async () => {
    // The dropdown is a portal jsdom will not open, so this drives the only
    // other path through confirm(): an unbound agent confirms as null rather
    // than as the sentinel the Select needs.
    const onChange = vi.fn();
    list.mockResolvedValue({ serviceAccounts: [LIVE] });
    renderControl({ value: null, onChange });

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(screen.queryByText('Loading service accounts…')).toBeNull()
    );
    fireEvent.click(screen.getByText('Choose'));

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('keeps the binding when a bound agent is confirmed unchanged', async () => {
    const onChange = vi.fn();
    list.mockResolvedValue({ serviceAccounts: [LIVE] });
    renderControl({ value: 'sa-live', onChange });

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(screen.queryByText('Loading service accounts…')).toBeNull()
    );
    fireEvent.click(screen.getByText('Choose'));

    expect(onChange).toHaveBeenCalledWith('sa-live');
  });

  it('offers to try again when the list cannot be loaded', async () => {
    list.mockRejectedValue(new Error('nope'));
    renderControl();

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(screen.getByText('Could not load service accounts.')).toBeTruthy()
    );
    expect(screen.getByText('Try again')).toBeTruthy();
  });
});

describe('what the save sends', () => {
  const flow = (): Parameters<typeof extractAgentConfigFromFlow>[1] => [
    { id: 'core', data: { type: 'agent-core', config: {} } },
  ];

  it('carries the binding for a service agent', () => {
    const payload = extractAgentConfigFromFlow(
      'Agent', flow(), [], null, true, true, 'sa-live'
    );
    expect(payload.serviceAccountUserId).toBe('sa-live');
  });

  it('sends null to clear a binding rather than dropping the field', () => {
    const payload = extractAgentConfigFromFlow(
      'Agent', flow(), [], null, true, true, null
    );
    expect('serviceAccountUserId' in payload).toBe(true);
    expect(payload.serviceAccountUserId).toBeNull();
  });

  it('leaves the binding alone when the choice was never offered', () => {
    // Undefined is not "unbind": a builder that never showed the control must
    // not clear an agent someone else bound.
    const payload = extractAgentConfigFromFlow(
      'Agent', flow(), [], null, true, true, undefined
    );
    expect('serviceAccountUserId' in payload).toBe(false);
  });

  it('never sends a binding for an agent that is not a service agent', () => {
    const payload = extractAgentConfigFromFlow(
      'Agent', flow(), [], null, false, false, 'sa-live'
    );
    expect('serviceAccountUserId' in payload).toBe(false);
  });
});
