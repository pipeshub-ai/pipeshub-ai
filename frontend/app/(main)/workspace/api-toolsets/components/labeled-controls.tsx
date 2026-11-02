'use client';

import type { ComponentProps } from 'react';
import { TextArea, TextField } from '@radix-ui/themes';
import { useFormFieldControl } from '../../components/form-field';

/** Inputs that take their id from the surrounding `FormField`, so its label names them. */
export function LabeledTextField(props: ComponentProps<typeof TextField.Root>) {
  return <TextField.Root {...useFormFieldControl()} {...props} />;
}

export function LabeledTextArea(props: ComponentProps<typeof TextArea>) {
  return <TextArea {...useFormFieldControl()} {...props} />;
}
