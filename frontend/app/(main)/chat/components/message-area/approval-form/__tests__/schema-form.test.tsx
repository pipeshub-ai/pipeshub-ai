import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { schemaFields } from '../schema-fields';
import { SchemaForm } from '../schema-form';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const fields = schemaFields({
  type: 'object',
  required: ['title'],
  properties: {
    title: { type: 'string' },
    count: { type: 'integer' },
    urgent: { type: 'boolean' },
    meta: { type: 'object' },
  },
});
const values = { title: 'Login fails', count: 2, urgent: false, meta: { a: 1 } };

beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

describe('SchemaForm', () => {
  it('labels every field and shows nested values read-only', () => {
    renderInTheme(<SchemaForm fields={fields} values={values} errors={{}} onChange={vi.fn()} />);

    expect((screen.getByRole('textbox', { name: /Title/ }) as HTMLInputElement).value).toBe('Login fails');
    expect((screen.getByRole('spinbutton', { name: 'Count' }) as HTMLInputElement).value).toBe('2');
    expect(screen.getByRole('switch', { name: 'Urgent' })).toBeTruthy();
    expect(screen.getByText("Can't be edited here")).toBeTruthy();
    expect(screen.getByText(/"a": 1/)).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Meta' })).toBeNull();
  });

  it('sends numbers as numbers, an emptied number as nothing, and text that is no number as typed', () => {
    const onChange = vi.fn();
    renderInTheme(<SchemaForm fields={fields} values={values} errors={{}} onChange={onChange} />);
    const count = screen.getByRole('spinbutton', { name: 'Count' });

    fireEvent.change(count, { target: { value: '7' } });
    expect(onChange).toHaveBeenLastCalledWith('count', 7);
    fireEvent.change(count, { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith('count', undefined);
  });

  it('marks changed fields and links each error to its field', () => {
    renderInTheme(
      <SchemaForm fields={fields} values={values} changed={new Set(['title'])} errors={{ title: 'This field is required.' }} onChange={vi.fn()} />,
    );

    expect(screen.getByText('Changed')).toBeTruthy();
    const title = screen.getByRole('textbox', { name: /Title/ });
    expect(title.getAttribute('aria-invalid')).toBe('true');
    expect(document.getElementById(title.getAttribute('aria-describedby') ?? '')?.textContent).toBe('This field is required.');
  });

  it('writes a switch back as a boolean', () => {
    const onChange = vi.fn();
    renderInTheme(<SchemaForm fields={fields} values={values} errors={{}} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Urgent' }));
    expect(onChange).toHaveBeenLastCalledWith('urgent', true);
  });
});
