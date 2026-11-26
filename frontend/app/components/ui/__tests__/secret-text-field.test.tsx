import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import {
  NO_CREDENTIAL_AUTOFILL_PROPS,
  SECRET_FIELD_MASK_CLASS,
  SecretTextField,
  secretInputProps,
} from '../secret-text-field';

afterEach(() => {
  cleanup();
});

function renderField(node: React.ReactElement) {
  const { container } = render(<Theme>{node}</Theme>);
  const input = container.querySelector('input');
  if (!input) throw new Error('no input rendered');
  return { container, input };
}

describe('SecretTextField', () => {
  it('never renders a password input, so browsers do not treat the secret as a login', () => {
    const { input } = renderField(<SecretTextField value="sk-123" onChange={() => {}} />);
    expect(input.type).toBe('text');
  });

  it('masks the value by default and opts out of autofill and password managers', () => {
    const { container, input } = renderField(<SecretTextField value="sk-123" onChange={() => {}} />);
    expect(container.querySelector(`.${SECRET_FIELD_MASK_CLASS}`)).not.toBeNull();
    expect(input.getAttribute('autocomplete')).toBe('off');
    expect(input.getAttribute('spellcheck')).toBe('false');
    expect(input.getAttribute('data-1p-ignore')).toBe('true');
    expect(input.getAttribute('data-lpignore')).toBe('true');
    expect(input.getAttribute('data-bwignore')).toBe('true');
    expect(input.getAttribute('data-form-type')).toBe('other');
  });

  it('shows the value when masked is false and still opts out of autofill', () => {
    const { container, input } = renderField(
      <SecretTextField masked={false} value="sk-123" onChange={() => {}} />,
    );
    expect(container.querySelector(`.${SECRET_FIELD_MASK_CLASS}`)).toBeNull();
    expect(input.type).toBe('text');
    expect(input.getAttribute('autocomplete')).toBe('off');
  });

  it('keeps a caller className next to the mask class', () => {
    const { container } = renderField(
      <SecretTextField className="extra" value="" onChange={() => {}} />,
    );
    const root = container.querySelector('.extra');
    expect(root?.classList.contains(SECRET_FIELD_MASK_CLASS)).toBe(true);
  });

  it('does not let a caller turn it back into a password field', () => {
    const props = { type: 'password' } as unknown as React.ComponentProps<typeof SecretTextField>;
    const { input } = renderField(<SecretTextField {...props} value="" onChange={() => {}} />);
    expect(input.type).toBe('text');
  });
});

describe('secretInputProps', () => {
  it('gives raw inputs the same treatment', () => {
    expect(secretInputProps()).toMatchObject({
      ...NO_CREDENTIAL_AUTOFILL_PROPS,
      type: 'text',
      spellCheck: false,
      className: SECRET_FIELD_MASK_CLASS,
    });
    expect(secretInputProps(false).className).toBeUndefined();
  });
});
