import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
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
