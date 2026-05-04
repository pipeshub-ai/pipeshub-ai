'use client';

import React, { useState } from 'react';
import {
  Dialog,
  Flex,
  Text,
  Button,
  Box,
  TextField,
  Spinner,
  VisuallyHidden,
} from '@radix-ui/themes';
import { ProfileApi, getUserIdFromToken, getUserEmailFromToken } from '@/app/(main)/workspace/profile/api';

// ========================================
// Types
// ========================================

export interface FullNameDialogProps {
  /** Controls visibility — set to true when user has no fullName */
  open: boolean;
  /** Called with the saved fullName so the parent can update the store */
  onSuccess: (fullName: string) => void;
}

// ========================================
// Component
// ========================================

/**
 * FullNameDialog — blocking modal that requires the user to set their full name
 * before continuing. It cannot be dismissed without saving a valid name.
 */
export function FullNameDialog({ open, onSuccess }: FullNameDialogProps) {
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState<string | undefined>(undefined);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // ── Shared dialog content style (matches the project-wide dialog pattern) ──

  const contentStyle: React.CSSProperties = {
    width: 480,
    maxWidth: 480,
    minWidth: 480,
    padding: 'var(--space-5)',
    backgroundColor: 'var(--color-panel-solid)',
    borderRadius: 'var(--radius-5)',
    border: '1px solid var(--olive-a3)',
    boxShadow:
      '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
    zIndex: 1000,
    overflow: 'hidden',
  };

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    const trimmed = fullName.trim();
    if (!trimmed) {
      setError('Full name is required');
      return;
    }
    if (trimmed.length < 2) {
      setError('Full name must be at least 2 characters');
      return;
    }

    setIsSubmitting(true);
    setError(undefined);

    try {
      const userId = getUserIdFromToken();
      const email = getUserEmailFromToken();

      if (!userId) {
        setError('Unable to identify user. Please refresh and try again.');
        return;
      }

      await ProfileApi.updateUser(userId, {
        fullName: trimmed,
        ...(email ? { email } : {}),
      });

      onSuccess(trimmed);
    } catch {
      setError('Failed to save your name. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isSubmitting) {
      void handleSubmit();
    }
  };

  // Intentionally prevent closing: onOpenChange is a no-op so Escape / outside
  // clicks cannot dismiss the dialog until the name is saved.
  const handleOpenChange = () => {};

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      {/* Dark overlay — no onClick so the user cannot dismiss by clicking outside */}
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.5)',
            zIndex: 999,
            cursor: 'default',
          }}
        />
      )}

      <Dialog.Content style={contentStyle}>
        <VisuallyHidden>
          <Dialog.Title>Complete Your Profile</Dialog.Title>
        </VisuallyHidden>

        <Flex direction="column" gap="4">
          {/* Header */}
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              Complete Your Profile
            </Text>
            <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
              Please set your full name to continue. This helps your teammates identify you across
              workspaces.
            </Text>
          </Flex>

          {/* Full name input */}
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Full name
            </Text>
            <TextField.Root
              placeholder="e.g. Jane Smith"
              value={fullName}
              onChange={(e) => {
                setFullName(e.target.value);
                if (error) setError(undefined);
              }}
              onKeyDown={handleKeyDown}
              color={error ? 'red' : undefined}
              disabled={isSubmitting}
              autoFocus
            />
            {error && (
              <Text size="1" style={{ color: 'var(--red-a11)' }}>
                {error}
              </Text>
            )}
          </Flex>

          {/* Submit button */}
          <Flex justify="end" style={{ marginTop: 'var(--space-1)' }}>
            <Button
              variant="solid"
              size="2"
              type="button"
              onClick={() => void handleSubmit()}
              disabled={isSubmitting || !fullName.trim()}
              style={{ cursor: isSubmitting ? 'not-allowed' : 'pointer', minWidth: 120 }}
            >
              {isSubmitting ? (
                <Flex align="center" gap="2">
                  <Spinner size="1" />
                  <span>Saving…</span>
                </Flex>
              ) : (
                'Save & continue'
              )}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
