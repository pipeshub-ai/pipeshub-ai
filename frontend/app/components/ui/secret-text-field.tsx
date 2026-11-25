'use client';

import { forwardRef } from 'react';
import { TextField } from '@radix-ui/themes';
import { useFormFieldControl } from '@/app/(main)/workspace/components/form-field';

/**
 * Field for a secret that is not the user's own sign-in password: an API key, a token,
 * a client secret, an SMTP or storage password.
 *
 * Browsers and password managers treat every password-type input as an account
 * login. They fill the saved email into the text field before it (a model name, an
 * endpoint) and the saved password into the secret, and after saving they offer to
 * store the secret as the site's password. `autocomplete="off"` does not stop this, and
 * `autocomplete="new-password"` stops the filling but not the save prompt. A text input
 * masked with CSS is not a password field, so no password manager acts on it.
 */

/** Masks a text input (or every input inside an element with the class). See globals.css. */
export const SECRET_FIELD_MASK_CLASS = 'secret-field-masked';

/** Keeps browser autofill and password-manager extensions out of a non-credential field. */
export const NO_CREDENTIAL_AUTOFILL_PROPS = {
  autoComplete: 'off',
  'data-1p-ignore': 'true',
  'data-lpignore': 'true',
  'data-bwignore': 'true',
  'data-form-type': 'other',
} as const;

/** Props for a raw `<input>` that holds a secret. `masked: false` shows the value. */
export function secretInputProps(masked = true) {
  return {
    ...NO_CREDENTIAL_AUTOFILL_PROPS,
    type: 'text' as const,
    spellCheck: false,
    autoCapitalize: 'off',
    autoCorrect: 'off',
    className: masked ? SECRET_FIELD_MASK_CLASS : undefined,
  };
}

export type SecretTextFieldProps = Omit<TextField.RootProps, 'type'> & {
  /** Hide the value behind dots. Pass `false` while the user has revealed it. */
  masked?: boolean;
};

export const SecretTextField = forwardRef<HTMLInputElement, SecretTextFieldProps>(
  function SecretTextField({ masked = true, className, ...rest }, ref) {
    // Inside a FormField the field's label and help name this input, as FormFieldTextField does; empty elsewhere.
    const control = useFormFieldControl();
    const { className: maskClass, ...secretProps } = secretInputProps(masked);
    const classes = [className, maskClass].filter(Boolean).join(' ') || undefined;
    return <TextField.Root ref={ref} {...control} {...rest} {...secretProps} className={classes} />;
  },
);
