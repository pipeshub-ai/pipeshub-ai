/**
 * AP-06 (C3.75), WP-50: edit before approving, on the chat's approval card. "Edit" is a third button beside
 * Allow once, offered only to the person the task runs for and only where the call offers editing. Editing happens
 * in the same card: changed values are marked with the old one struck through; lists and nested values say "Can't
 * be edited here"; Undo changes / Cancel / Approve with changes; Escape cancels and Ctrl/Cmd+Enter approves. The
 * receipt says "Approved with your changes" with old and new lines and "Show everything that was sent".
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';
import { ApprovalCard, EDIT_HAS_SECRET } from '../approval-card';
import { ApprovalInteraction } from '../interaction-card';
import type { PendingApproval } from '../../../types';
import { VECTOR_EXAMPLES } from '../../../__tests__/token-vector';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const ARGS = {
  to: 'all-staff@acme.com',
  subject: 'Q3 results',
  attachments: ['deck.pdf'],
};

const held = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.approve.t1.0',
  toolCallId: 'call-1',
  toolName: 'gmail__send_email',
  args: JSON.stringify(ARGS),
  argsSummary: null,
  reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false, editable: true },
  status: 'pending',
  errorCode: null,
  ...over,
});

const renderCard = (props: Partial<React.ComponentProps<typeof ApprovalCard>> = {}) => {
  const onApproveEdited = vi.fn();
  const onDecide = vi.fn();
  render(
    <Theme>
      <ApprovalCard approval={held()} onDecide={onDecide} editAllowed onApproveEdited={onApproveEdited} {...props} />
    </Theme>,
  );
  return { onApproveEdited, onDecide };
};

const input = (label: string) => screen.getByLabelText(new RegExp(`^${label}`), { selector: 'input,textarea' });

describe('AP-06 the card offers Edit', () => {
  it('AP-06: Edit is a third button between Deny and Allow once', () => {
    renderCard();
    const buttons = screen.getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Deny', 'Edit', 'Allow once']);
  });

  it('AP-06: no Edit for an approver, for a call that offers none, or after the server said editing is not theirs', () => {
    renderCard({ editAllowed: false });
    expect(screen.queryByTestId('approval-edit')).toBeNull();
    cleanup();
    renderCard({ approval: held({ reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false } }) });
    expect(screen.queryByTestId('approval-edit')).toBeNull();
    cleanup();
    renderCard({ approval: held({ errorCode: harnessHttpCode('EDIT_NOT_ALLOWED') }) });
    expect(screen.queryByTestId('approval-edit')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('Only the person this task runs for');
  });
});

describe('AP-06 editing in place', () => {
  it('AP-06: a changed value is marked with the old one struck through; a list cannot be edited here', () => {
    renderCard();
    fireEvent.click(screen.getByTestId('approval-edit'));
    expect(screen.getByTestId('approval-edit-form')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Allow once/ })).toBeNull();
    const fixed = screen.getByTestId('approval-edit-fixed');
    expect(fixed.textContent).toContain('deck.pdf');
    expect(fixed.textContent).toContain("Can't be edited here");
    expect((screen.getByTestId('approval-edit-approve') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input('To'), { target: { value: 'leadership@acme.com' } });
    const field = screen.getAllByTestId('approval-edit-field').find((el) => el.dataset.changed === 'true');
    expect(field?.textContent).toContain('changed');
    expect(within(field as HTMLElement).getByTestId('approval-edit-was').textContent).toBe('all-staff@acme.com');
  });

  it('AP-06: Approve with changes sends the whole arguments with the change, in their own types', () => {
    const { onApproveEdited, onDecide } = renderCard();
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.change(input('To'), { target: { value: 'leadership@acme.com' } });
    fireEvent.click(screen.getByTestId('approval-edit-approve'));
    expect(onApproveEdited).toHaveBeenCalledWith({ ...ARGS, to: 'leadership@acme.com' });
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('AP-06: Undo changes restores the values; Cancel and Escape leave editing; Ctrl/Cmd+Enter approves', () => {
    const { onApproveEdited } = renderCard();
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.change(input('Subject'), { target: { value: 'Q3' } });
    fireEvent.click(screen.getByTestId('approval-edit-undo'));
    expect((input('Subject') as HTMLInputElement).value).toBe('Q3 results');
    fireEvent.click(screen.getByTestId('approval-edit-cancel'));
    expect(screen.queryByTestId('approval-edit-form')).toBeNull();
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.keyDown(input('Subject'), { key: 'Escape' });
    expect(screen.queryByTestId('approval-edit-form')).toBeNull();
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.change(input('Subject'), { target: { value: 'Q3 numbers' } });
    fireEvent.keyDown(input('Subject'), { key: 'Enter', metaKey: true });
    expect(onApproveEdited).toHaveBeenCalledWith({ ...ARGS, subject: 'Q3 numbers' });
  });

  it('AP-06 (EDIT_HAS_SECRET): a pasted key is refused on the card, which points at the Actions page', () => {
    const [{ secret }] = VECTOR_EXAMPLES;
    const { onApproveEdited } = renderCard();
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.change(input('Subject'), { target: { value: `key ${secret}` } });
    expect(screen.getByTestId('approval-edit-secret').textContent).toContain('Save it as a credential');
    expect(screen.getByRole('link', { name: 'Open Actions' }).getAttribute('href')).toBe('/workspace/actions/');
    expect((screen.getByTestId('approval-edit-approve') as HTMLButtonElement).disabled).toBe(true);
    expect(onApproveEdited).not.toHaveBeenCalled();
    cleanup();
    renderCard({ approval: held({ errorCode: EDIT_HAS_SECRET }) });
    expect(screen.getByTestId('approval-edit-secret')).toBeTruthy();
  });
});

describe('AP-06 the receipt', () => {
  const edited = held({ status: 'approved', editedArgs: JSON.stringify({ ...ARGS, to: 'leadership@acme.com' }) });

  it('AP-06: "Approved with your changes", old and new lines, and everything that was sent on request', () => {
    render(
      <Theme>
        <ApprovalCard approval={edited} />
      </Theme>,
    );
    expect(screen.getAllByText('Approved with your changes').length).toBeGreaterThan(0);
    const [line] = screen.getAllByTestId('approval-edited-line');
    expect(line.textContent).toContain('all-staff@acme.com');
    expect(line.textContent).toContain('leadership@acme.com');
    expect(screen.queryByTestId('approval-preview')).toBeNull();
    fireEvent.click(screen.getByTestId('approval-edited-all'));
    expect(screen.getByTestId('approval-preview').textContent).toContain('leadership@acme.com');
  });

  it('AP-06: the chat receipt line says so, with the change under it', () => {
    render(
      <Theme>
        <ApprovalInteraction
          group={{ key: 'g', items: [edited] }}
          state="done"
          onDecide={vi.fn()}
          onDecideAll={vi.fn()}
          conversationChoice
          paused={false}
        />
      </Theme>,
    );
    expect(screen.getByTestId('interaction-receipt').textContent).toContain('Approved with your changes');
    expect(screen.getByTestId('approval-edited-receipt').textContent).toContain('leadership@acme.com');
  });

  it('AP-06: the conversation chat offers Edit only to the run’s actor (authority.edit)', () => {
    const onApproveEdited = vi.fn();
    const props = {
      group: { key: 'g', items: [held()] },
      state: 'active' as const,
      onDecide: vi.fn(),
      onDecideAll: vi.fn(),
      onApproveEdited,
      conversationChoice: true,
      paused: false,
    };
    render(
      <Theme>
        <ApprovalInteraction {...props} authority={{ approve: true, deny: true, edit: false }} />
      </Theme>,
    );
    expect(screen.queryByTestId('approval-edit')).toBeNull();
    cleanup();
    render(
      <Theme>
        <ApprovalInteraction {...props} authority={{ approve: true, deny: true, edit: true }} />
      </Theme>,
    );
    fireEvent.click(screen.getByTestId('approval-edit'));
    fireEvent.change(input('To'), { target: { value: 'x@acme.com' } });
    fireEvent.click(screen.getByTestId('approval-edit-approve'));
    expect(onApproveEdited).toHaveBeenCalledWith(props.group.items[0], { ...ARGS, to: 'x@acme.com' });
  });
});
