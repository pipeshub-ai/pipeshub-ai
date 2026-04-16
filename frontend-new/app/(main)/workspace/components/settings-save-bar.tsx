'use client';

import React, { useState } from 'react';
import { Flex, Button } from '@radix-ui/themes';

// ========================================
// Types
// ========================================

export interface SettingsSaveBarProps {
  /** Whether the bar is visible (form has unsaved changes) */
  visible: boolean;
  /** Called when the "Discard changes" button is clicked */
  onDiscard: () => void;
  /** Called when the "Save" button is clicked */
  onSave: () => void | Promise<void>;
  /** External loading state (e.g. from an API call in the parent) */
  isSaving?: boolean;
  /** Label for the save button — defaults to "Save" */
  saveLabel?: string;
}

// ========================================
// Component
// ========================================

/**
 * SettingsSaveBar — floating bottom bar shown when a settings form has
 * unsaved changes.
 *
 * Mirrors the positioning pattern of EntityBulkActionBar (absolute, centered
 * at bottom of its positioned container).
 *
 * Usage:
 * - Wrap the page in `position: relative; height: 100%; overflow-y: auto`
 * - Render <SettingsSaveBar> as a direct child of that wrapper
 * - Pass `visible={isDirty}` to toggle visibility
 *
 * Reusable across: General, Profile, Authentication, AI Models, and any
 * other settings screen that has an editable form.
 */
export function SettingsSaveBar({
  visible,
  onDiscard,
  onSave,
  isSaving = false,
  saveLabel = 'Save',
}: SettingsSaveBarProps) {
  const [localSaving, setLocalSaving] = useState(false);

  const isActuallySaving = isSaving || localSaving;

  const handleSave = async () => {
    if (isActuallySaving) return;
    setLocalSaving(true);
    try {
      await onSave();
    } finally {
      setLocalSaving(false);
    }
  };

  return (
    <Flex
      align="center"
      gap="2"
      style={{
        position: 'fixed',
        bottom: 'var(--space-4)',
        left: '50%',
        transform: visible ? 'translateX(-50%) translateY(0)' : 'translateX(-50%) translateY(100%)',
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? 'auto' : 'none',
        transition: 'transform 0.3s ease, opacity 0.3s ease',
        width: 'fit-content',
        background: 'var(--effects-translucent)',
        backdropFilter: 'blur(25px)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-2)',
        zIndex: 20,
        boxShadow: '0 20px 28px 0 rgba(0, 0, 0, 0.15)',
        whiteSpace: 'nowrap',
      }}
    >
      <Button
        size="2"
        variant="outline"
        color="gray"
        onClick={onDiscard}
        disabled={isActuallySaving}
        style={{ cursor: 'pointer' }}
      >
        Discard changes
      </Button>
      <Button
        size="2"
        variant="solid"
        onClick={handleSave}
        disabled={isActuallySaving}
        style={{ cursor: isActuallySaving ? 'wait' : 'pointer' }}
      >
        {isActuallySaving ? 'Saving...' : saveLabel}
      </Button>
    </Flex>
  );
}


