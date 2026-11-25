/**
 * WP-63 (GITF-28, SITE-UP-1..3): the clone card and the upload card on the existing approval card. A clone names
 * its repository, says what it brings (public, read-only, its size) and offers Allow once, Allow this repository for
 * this conversation, and Deny; on a phone the conversation button is short. The upload card asks
 * "Allow uploads to <site> for this run?" with Allow for this run and Deny only, and lists what the run read.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { cloneDetails, interactionTitle } from '../../../interaction-wording';
import type { ApprovalReason, PendingApproval } from '../../../types';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

const h = React.createElement;
const t = i18n.t.bind(i18n);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  mobile.value = false;
});

const CLONE_REASON: ApprovalReason = {
  rule: 'tool',
  layer: 'engine',
  sensitivity: 'public',
  sources: [],
  moreSources: 0,
  destination: 'github.com/onyx-dot-app/onyx (public, read-only, about 42 MB)',
  conversationGrant: true,
};

const UPLOAD_REASON: ApprovalReason = {
  rule: 'site_upload',
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
  moreSources: 0,
  destination: 'mirror.acme.net',
  conversationGrant: false,
};

const approval = (overrides: Partial<PendingApproval>): PendingApproval => ({
  interactionId: 'ix.approve.t1.0',
  toolCallId: 'call-1',
  toolName: 'pipeshub__repository_fetch',
  args: JSON.stringify({ url: 'https://github.com/onyx-dot-app/onyx' }),
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  reason: CLONE_REASON,
  ...overrides,
});

function renderCard(card: PendingApproval) {
  const onDecide = vi.fn();
  render(h(Theme, null, h(ApprovalCard, { approval: card, onDecide, conversationChoice: true })));
  return onDecide;
}

describe('clone card (WP-63)', () => {
  it('GITF-28: the title names the repository; the body says public, read-only and the size', () => {
    renderCard(approval({}));
    expect(screen.getByRole('region', { name: 'Clone github.com/onyx-dot-app/onyx?' })).toBeTruthy();
    expect(screen.getByTestId('clone-summary').textContent).toBe(
      "Public, read-only, about 42 MB. PipesHub copies it into the agent's workspace; nothing is sent back to github.com.",
    );
    expect(screen.queryByTestId('approval-preview')).toBeNull();
  });

  it('GITF-28: a first clone says "up to" the most a clone may bring', () => {
    renderCard(approval({ reason: { ...CLONE_REASON, destination: 'github.com/acme/widgets (public, read-only, up to 500 MB)' } }));
    expect(screen.getByTestId('clone-summary').textContent).toContain('Public, read-only, up to 500 MB.');
  });

  it('GITF-28: three buttons, each deciding as named', () => {
    const onDecide = renderCard(approval({}));
    expect(screen.getByRole('button', { name: /Deny/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Allow this repository for this conversation' }));
    expect(onDecide).toHaveBeenCalledWith('approved', 'conversation');
    fireEvent.click(screen.getByRole('button', { name: /^Allow once: / }));
    expect(onDecide).toHaveBeenLastCalledWith('approved');
  });

  it('GITF-28: on a phone the conversation button is short and the private-read warning is one line', () => {
    mobile.value = true;
    renderCard(approval({ reason: { ...CLONE_REASON, sensitivity: 'private' } }));
    // The words are short; a screen reader still hears what the button covers in full.
    expect(screen.getByRole('button', { name: 'Allow this repository for this conversation' }).textContent).toBe('Allow this repository here');
    expect(screen.getByTestId('clone-summary').textContent).toContain('Public, read-only, about 42 MB.');
    expect(screen.getByTestId('clone-private-read').textContent).toBe('This conversation has read private data.');
  });

  it('GITF-28: after a private read, the desktop card says the address is sent to the host', () => {
    renderCard(approval({ reason: { ...CLONE_REASON, sensitivity: 'private' } }));
    expect(screen.getByTestId('clone-private-read').textContent).toBe(
      'This conversation has read private data. The repository address is sent to github.com.',
    );
  });

  it('cloneDetails splits the destination; a destination without a size keeps the repository', () => {
    expect(cloneDetails('github.com/o/r (public, read-only, about 3 MB)')).toEqual({
      repository: 'github.com/o/r',
      host: 'github.com',
      size: { kind: 'about', mb: 3 },
    });
    expect(cloneDetails('gitlab.com/o/r')).toEqual({ repository: 'gitlab.com/o/r', host: 'gitlab.com', size: null });
  });
});

describe('upload card (WP-63)', () => {
  const upload = approval({ toolName: 'site_upload', args: '{}', reason: UPLOAD_REASON });

  it('SITE-UP-1: titled for the site and the run, with why it asks and what the run read', () => {
    renderCard(upload);
    expect(screen.getByRole('region', { name: 'Allow uploads to mirror.acme.net for this run?' })).toBeTruthy();
    expect(screen.getByTestId('approval-why').textContent).toBe(
      'This conversation read private data, so requests to mirror.acme.net wait for you: a link or an upload can carry what it read. Package installs keep working meanwhile.',
    );
    expect(screen.getByTestId('approval-sources').textContent).toContain('Q3 plan');
    expect(screen.queryByTestId('approval-preview')).toBeNull();
  });

  it('SITE-UP-2: Allow for this run and Deny only; never a conversation grant', () => {
    const onDecide = renderCard({ ...upload, reason: { ...UPLOAD_REASON, conversationGrant: true } });
    expect(screen.queryByRole('button', { name: /conversation/ })).toBeNull();
    const allow = screen.getByRole('button', { name: 'Allow for this run: mirror.acme.net' });
    expect(allow.textContent).toBe('Allow for this run');
    fireEvent.click(allow);
    expect(onDecide).toHaveBeenCalledWith('approved');
    expect(screen.getByRole('button', { name: /Deny/ })).toBeTruthy();
  });

  it('SITE-UP-3: the title is the same wherever the card shows (inbox, link)', () => {
    expect(interactionTitle(t, { toolName: 'site_upload', reason: UPLOAD_REASON })).toBe('Allow uploads to mirror.acme.net for this run?');
  });
});
