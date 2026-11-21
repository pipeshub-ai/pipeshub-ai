import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

import { FormField, LabeledTextField } from '../form-field';
import { useFormValidation } from '../use-form-validation';

afterEach(() => cleanup());

describe('useFormValidation', () => {
  it('hides a rule problem until the field is touched, but is invalid straight away', () => {
    const { result } = renderHook(() => useFormValidation<'name'>({ name: 'Name is required.' }));
    expect(result.current.isValid).toBe(false);
    expect(result.current.errorFor('name')).toBeUndefined();
    act(() => result.current.touch('name'));
    expect(result.current.errorFor('name')).toBe('Name is required.');
  });

  it('is valid when no rule has a problem', () => {
    const { result } = renderHook(() => useFormValidation<'name'>({ name: undefined }));
    expect(result.current.isValid).toBe(true);
  });

  it('shows a server field error at once, and drops it when the field changes', () => {
    const { result } = renderHook(() => useFormValidation<'name' | 'description'>({}));
    let mapped = false;
    act(() => {
      mapped = result.current.applyServerErrors({ name: 'Group already exists.' }, ['name', 'description']);
    });
    expect(mapped).toBe(true);
    expect(result.current.errorFor('name')).toBe('Group already exists.');
    act(() => result.current.clearServerError('name'));
    expect(result.current.errorFor('name')).toBeUndefined();
  });

  it('reports false when the server named only fields this form does not have', () => {
    const { result } = renderHook(() => useFormValidation<'name'>({}));
    let mapped = true;
    act(() => {
      mapped = result.current.applyServerErrors({ somethingElse: 'x' }, ['name']);
    });
    expect(mapped).toBe(false);
  });
});

describe('FormField with LabeledTextField', () => {
  function Demo() {
    const v = useFormValidation<'name'>({ name: 'Name is required.' });
    return (
      <Theme>
        <FormField label="Name" required error={v.errorFor('name')}>
          <LabeledTextField onBlur={() => v.touch('name')} />
        </FormField>
      </Theme>
    );
  }

  it('links the error to the input and marks it invalid after blur', () => {
    render(<Demo />);
    const input = screen.getByLabelText(/^Name/);
    expect(input.getAttribute('aria-invalid')).toBeNull();
    expect(input.getAttribute('aria-required')).toBe('true');
    fireEvent.blur(input);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toBe('Name is required.');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(input.getAttribute('aria-describedby')).toContain(alert.id);
  });
});

/**
 * A field is left by pressing a button below it. An error line shown on blur would push that button down
 * between mouse-down and mouse-up and lose the press, so the error waits until the press is over.
 */
describe('an error asked for while a press is on', () => {
  const PROBLEM = 'Each redirect URI must be a valid http(s) URL.';

  function Form() {
    const [value, setValue] = React.useState('');
    const [added, setAdded] = React.useState(0);
    const v = useFormValidation<'uri'>({ uri: value.startsWith('https://') ? undefined : PROBLEM });
    return (
      <Theme>
        <input aria-label="Redirect URI" value={value} onChange={(e) => setValue(e.target.value)} onBlur={() => v.touch('uri')} />
        {v.errorFor('uri') && <p role="alert">{v.errorFor('uri')}</p>}
        <button type="button" onClick={() => setAdded((n) => n + 1)}>
          Add redirect URI
        </button>
        <output aria-label="added">{added}</output>
        <button type="button" onClick={() => v.reset()}>
          Cancel
        </button>
      </Theme>
    );
  }

  function leaveBy(button: HTMLElement, input: HTMLElement, { click = true } = {}) {
    fireEvent.pointerDown(button);
    fireEvent.blur(input);
    const during = screen.queryByRole('alert');
    fireEvent.pointerUp(button);
    if (click) fireEvent.click(button);
    return during;
  }

  it('shows the error once the press that left the field is over, after its click has landed', async () => {
    render(<Form />);
    const input = screen.getByLabelText('Redirect URI');
    fireEvent.change(input, { target: { value: 'http://e2e.example.com/oauth/callback' } });
    const during = leaveBy(screen.getByRole('button', { name: 'Add redirect URI' }), input);
    expect(during, 'nothing moves under the pointer while it is pressed').toBeNull();
    expect(screen.getByLabelText('added').textContent).toBe('1');
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(PROBLEM));
  });

  it('shows it at once when the field is left with the keyboard', () => {
    render(<Form />);
    fireEvent.blur(screen.getByLabelText('Redirect URI'));
    expect(screen.getByRole('alert').textContent).toBe(PROBLEM);
  });

  it('shows it a moment after a release that brings no click', async () => {
    render(<Form />);
    const input = screen.getByLabelText('Redirect URI');
    expect(leaveBy(document.body, input, { click: false })).toBeNull();
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(PROBLEM), { timeout: 2000 });
  });

  it('drops the waiting error when that press resets the form', async () => {
    render(<Form />);
    const input = screen.getByLabelText('Redirect URI');
    leaveBy(screen.getByRole('button', { name: 'Cancel' }), input);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
