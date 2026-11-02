import React, { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { schemaFields } from '../schema-fields';
import { DocumentForm } from '../document-form';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const fields = schemaFields({
  type: 'object',
  required: ['to', 'subject'],
  properties: {
    to: { type: 'array', items: { type: 'string' } },
    cc: { type: 'array', items: { type: 'string', format: 'email' } },
    subject: { type: 'string' },
    body: { type: 'string' },
    priority: { type: 'integer' },
    meta: { type: 'object' },
  },
});

function Harness({ initial, errors = {}, onChange = vi.fn() }: { initial: Record<string, unknown>; errors?: Record<string, string>; onChange?: (n: string, v: unknown) => void }) {
  const [values, setValues] = useState(initial);
  return (
    <DocumentForm
      fields={fields}
      values={values}
      errors={errors}
      onChange={(name, value) => {
        onChange(name, value);
        setValues((prev) => ({ ...prev, [name]: value }));
      }}
    />
  );
}

const recipientInput = (label: string) => screen.getByRole('group', { name: label }).querySelector('input') as HTMLInputElement;

beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

describe('DocumentForm', () => {
  it('reads as the message: recipients, subject, body, then pills and more fields', () => {
    renderInTheme(<Harness initial={{ to: ['U123'], subject: 'Hello', body: 'Hi there' }} />);

    expect(screen.getByRole('group', { name: 'To' })).toBeTruthy();
    expect((screen.getByRole('textbox', { name: 'Subject' }) as HTMLInputElement).value).toBe('Hello');
    expect((screen.getByRole('textbox', { name: 'Body' }) as HTMLTextAreaElement).value).toBe('Hi there');
    expect(screen.getByRole('button', { name: 'Priority: not set' })).toBeTruthy();
    expect(screen.queryByText("Can't be edited here")).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More fields' }));
    expect(screen.getByText("Can't be edited here")).toBeTruthy();
  });

  it('chip row adds on Enter and removes on ×', () => {
    const onChange = vi.fn();
    renderInTheme(<Harness initial={{ to: ['U123'], subject: 'Hello' }} onChange={onChange} />);

    const input = recipientInput('To');
    fireEvent.change(input, { target: { value: 'U456' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith('to', ['U123', 'U456']);

    const remove = screen.getByRole('button', { name: 'Remove U123' });
    remove.focus();
    expect(document.activeElement).toBe(remove);
    fireEvent.click(remove);
    expect(onChange).toHaveBeenLastCalledWith('to', ['U456']);
  });

  it('ids under to are not flagged', () => {
    renderInTheme(<Harness initial={{ to: ['U123', 'not an email'], subject: 'Hello' }} />);
    expect(screen.getByRole('group', { name: 'To' }).getAttribute('aria-invalid')).toBeNull();
    expect(screen.queryByText('Enter a valid email address.')).toBeNull();
  });

  it('format email flags an invalid address', () => {
    renderInTheme(<Harness initial={{ to: [], subject: 'Hello', cc: ['sam@example.com', 'sam@'] }} />);
    expect(screen.getByRole('group', { name: 'Cc' }).getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Enter a valid email address.')).toBeTruthy();
  });

  it('pill opens a popover editor that writes the value back', () => {
    const onChange = vi.fn();
    renderInTheme(<Harness initial={{ to: [], subject: 'Hello' }} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'Priority: not set' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Priority' }), { target: { value: '3' } });
    expect(onChange).toHaveBeenLastCalledWith('priority', 3);
    expect(screen.getByRole('button', { name: /^Priority: 3/ })).toBeTruthy();
  });

  it('server error lands on its pill with a red hairline and message', () => {
    renderInTheme(<Harness initial={{ to: [], subject: 'Hello', priority: 9 }} errors={{ priority: 'Too high' }} />);

    const pill = screen.getByRole('button', { name: /^Priority: 9/ });
    expect(pill.getAttribute('aria-invalid')).toBe('true');
    expect(pill.style.border).toContain('var(--red-8)');
    expect(document.getElementById(pill.getAttribute('aria-describedby') ?? '')?.textContent).toBe('Priority: Too high');

    fireEvent.click(pill);
    expect(screen.getAllByText('Too high').length).toBe(1);
    expect(screen.getByRole('spinbutton', { name: 'Priority' }).getAttribute('aria-invalid')).toBe('true');
  });

  it('an enum pill opens a list and writes back the value as it was', async () => {
    const pick = schemaFields({ type: 'object', properties: { level: { type: 'integer', enum: [1, 2] } } });
    const onChange = vi.fn();
    renderInTheme(<DocumentForm fields={pick} values={{ level: 1 }} errors={{}} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole('button', { name: /^Level: 1/ }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitemradio', { name: '2' }));
    expect(onChange).toHaveBeenCalledWith('level', 2);
  });

  it('a pill is a Radix button with a 28px hairline, not a hand-rolled one', () => {
    renderInTheme(<Harness initial={{ to: [], subject: 'Hello' }} />);
    const pill = screen.getByRole('button', { name: 'Priority: not set' });
    expect(pill.className).toMatch(/rt-Button/);
    expect(pill.style.borderRadius).toBe('var(--radius-2)');
    expect(pill.style.border).toContain('var(--gray-6)');
  });

  it('labels and other lists are a labelled chip row, never a pill, and never checked as addresses', () => {
    const list = schemaFields({ type: 'object', properties: { labels: { type: 'array', items: { type: 'string' } } } });
    renderInTheme(<DocumentForm fields={list} values={{ labels: ['bug', 'not an email'] }} errors={{}} onChange={vi.fn()} />);
    const row = screen.getByRole('group', { name: 'Labels' });
    expect(row.getAttribute('aria-invalid')).toBeNull();
    expect(screen.getByText('bug')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Labels:/ })).toBeNull();
  });

  it('an unset optional yes/no pill says not set until it is changed', () => {
    const toggle = schemaFields({ type: 'object', properties: { urgent: { type: 'boolean' } } });
    const onChange = vi.fn();
    renderInTheme(<DocumentForm fields={toggle} values={{}} errors={{}} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Urgent: not set' }));
    expect(onChange).toHaveBeenCalledWith('urgent', true);
  });

  it('an optional enum pill can be cleared back to unset; a required one cannot', async () => {
    const pick = schemaFields({
      type: 'object',
      required: ['kind'],
      properties: { level: { type: 'string', enum: ['High', 'Low'] }, kind: { type: 'string', enum: ['Bug', 'Task'] } },
    });
    const onChange = vi.fn();
    renderInTheme(<DocumentForm fields={pick} values={{ level: 'High', kind: 'Bug' }} errors={{}} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole('button', { name: /^Level: High/ }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Clear' }));
    expect(onChange).toHaveBeenCalledWith('level', undefined);
    fireEvent.keyDown(screen.getByRole('button', { name: /^Kind: Bug/ }), { key: 'Enter' });
    await screen.findByRole('menuitemradio', { name: 'Task' });
    expect(screen.queryByRole('menuitem', { name: 'Clear' })).toBeNull();
  });

  it('an invalid headline is marked for its red hairline', () => {
    renderInTheme(<Harness initial={{ to: [], subject: '' }} errors={{ subject: 'Required' }} />);
    const headline = screen.getByRole('textbox', { name: 'Subject' });
    expect(headline.className).toContain('tool-approval-headline');
    expect(headline.getAttribute('aria-invalid')).toBe('true');
  });

  it('a yes/no pill toggles in place', () => {
    const toggle = schemaFields({ type: 'object', properties: { urgent: { type: 'boolean' } } });
    const onChange = vi.fn();
    renderInTheme(<DocumentForm fields={toggle} values={{ urgent: false }} errors={{}} onChange={onChange} />);
    const pill = screen.getByRole('button', { name: 'Urgent: No' });
    expect(pill.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(pill);
    expect(onChange).toHaveBeenCalledWith('urgent', true);
  });
});
