'use client';

import React from 'react';
import { Button } from '@radix-ui/themes';
import { Spinner } from './spinner';

type RadixButtonProps = React.ComponentProps<typeof Button>;

export interface LoadingButtonProps extends Omit<RadixButtonProps, 'loading'> {
  /** Show spinner, disable button, keep width. */
  loading?: boolean;
  /** Optional text shown next to the spinner instead of children when loading. */
  loadingLabel?: React.ReactNode;
  /** Spinner diameter in px. Default: 14 */
  spinnerSize?: number;
}

/**
 * LoadingButton — drop-in replacement for Radix `Button` that shows a
 * spinner while `loading` is true. The button stays the same width because
 * the spinner replaces the first child inline.
 *
 * Usage:
 *   <LoadingButton loading={isSubmitting} onClick={submit}>Save</LoadingButton>
 */
export function LoadingButton({
  loading = false,
  loadingLabel,
  spinnerSize = 14,
  disabled,
  children,
  style,
  ...rest
}: LoadingButtonProps) {
  const isDisabled = disabled || loading;

  return (
    <Button
      {...rest}
      disabled={isDisabled}
      style={{
        cursor: isDisabled ? 'not-allowed' : 'pointer',
        gap: 6,
        ...style,
      }}
    >
      {loading ? (
        <>
          <Spinner size={spinnerSize} />
          {loadingLabel ?? children}
        </>
      ) : (
        children
      )}
    </Button>
  );
}
