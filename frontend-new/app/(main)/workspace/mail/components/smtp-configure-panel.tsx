'use client';

import React, { useState, useCallback, useEffect } from 'react';
import { Flex, Box, Text, TextField, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '../../components/workspace-right-panel';
import { isValidEmail } from '@/lib/utils/validators';
import type { SmtpConfig, SmtpFormData, SmtpFormErrors } from '../types';

// ============================================================
// Types
// ============================================================

interface SmtpConfigurePanelProps {
  open: boolean;
  isConfigured: boolean;
  onClose: () => void;
  onSaveSuccess: () => void;
  /** Pre-fill from API */
  initialConfig: SmtpConfig | null;
  onSave: (config: SmtpConfig) => Promise<void>;
}

// ============================================================
// Helpers
// ============================================================

function validate(form: SmtpFormData): SmtpFormErrors {
  const errors: SmtpFormErrors = {};
  if (!form.host.trim()) errors.host = 'SMTP host is required';
  if (form.port === '' || form.port === undefined) {
    errors.port = 'Port is required';
  } else {
    const p = Number(form.port);
    if (!Number.isInteger(p) || p < 1 || p > 65535) errors.port = 'Enter a valid port (1–65535)';
  }
  if (!form.fromEmail.trim()) {
    errors.fromEmail = 'From email is required';
  } else if (!isValidEmail(form.fromEmail.trim())) {
    errors.fromEmail = 'Enter a valid email address';
  }
  return errors;
}

// ============================================================
// Label + hint helper sub-component
// ============================================================

interface FieldLabelProps {
  label: string;
  hint?: string;
}

function FieldLabel({ label, hint }: FieldLabelProps) {
  return (
    <Box style={{ marginBottom: 6 }}>
      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {label}
      </Text>
      {hint && (
        <Text size="1" style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2 }}>
          {hint}
        </Text>
      )}
    </Box>
  );
}

// ============================================================
// Component
// ============================================================

export function SmtpConfigurePanel({
  open,
  onClose,
  onSaveSuccess,
  initialConfig,
  onSave,
}: SmtpConfigurePanelProps) {
  const [form, setForm] = useState<SmtpFormData>({
    host: '',
    port: 587,
    fromEmail: '',
    username: '',
    password: '',
  });
  const [errors, setErrors] = useState<SmtpFormErrors>({});
  const [isSaving, setIsSaving] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // ── Sync initial config in ──────────────────────────────
  useEffect(() => {
    if (!open) return;
    if (initialConfig) {
      setForm({
        host: initialConfig.host ?? '',
        port: initialConfig.port ?? 587,
        fromEmail: initialConfig.fromEmail ?? '',
        username: initialConfig.username ?? '',
        password: initialConfig.password ?? '',
      });
    } else {
      setForm({ host: '', port: 587, fromEmail: '', username: '', password: '' });
    }
    setErrors({});
    setShowPassword(false);
  }, [open, initialConfig]);

  // ── Field change handler ────────────────────────────────
  const handleChange = useCallback(
    (field: keyof SmtpFormData) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      setForm((prev) => ({
        ...prev,
        [field]: field === 'port' ? (raw === '' ? '' : parseInt(raw, 10) || '') : raw,
      }));
      setErrors((prev) => ({ ...prev, [field]: undefined }));
    },
    [],
  );

  // ── Save ────────────────────────────────────────────────
  const handleSave = async () => {
    const errs = validate(form);
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }

    setIsSaving(true);
    try {
      const payload: SmtpConfig = {
        host: form.host.trim(),
        port: Number(form.port),
        fromEmail: form.fromEmail.trim(),
        ...(form.username.trim() ? { username: form.username.trim() } : {}),
        ...(form.password ? { password: form.password } : {}),
      };
      await onSave(payload);
      onSaveSuccess();
      onClose();
    } finally {
      setIsSaving(false);
    }
  };

  // ── Doc button ──────────────────────────────────────────
  const docButton = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      onClick={() => window.open('https://docs.pipeshub.com/smtp', '_blank')}
      style={{ cursor: 'pointer', gap: 'var(--space-1)' }}
    >
      <span className="material-icons-outlined" style={{ fontSize: 14 }}>open_in_new</span>
      <Text size="1">Documentation</Text>
    </Button>
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title="Configure SMTP Settings"
      icon="mail"
      headerActions={docButton}
      primaryLabel="Save"
      secondaryLabel="Cancel"
      primaryDisabled={false}
      primaryLoading={isSaving}
      onPrimaryClick={handleSave}
      onSecondaryClick={onClose}
      iconSize={20}
    >
      <Flex direction="column" gap="5">
        {/* ── Info banner ── */}
        <Box
          style={{
            background: 'var(--olive-2)',
            border: '1px solid var(--accent-3)',
            borderRadius: 'var(--radius-2)',
            overflow: 'hidden',
          }}
        >
          <Flex
            align="center"
            gap="3"
            style={{
              background: 'var(--accent-a3)',
              padding: '10px 12px',
            }}
          >
            <Box
              style={{
                background: 'var(--olive-a3)',
                borderRadius: 'var(--radius-1)',
                padding: 8,
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <MaterialIcon name="info" size={16} color="var(--accent-9)" />
            </Box>
            <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '18px' }}>
              SMTP configuration is required for email-based features like OTP authentication and
              password reset. Please enter the details of your email server below.
            </Text>
          </Flex>
        </Box>

        {/* ── Fields box ── */}
        <Box
          style={{
            background: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-4)',
          }}
        >
          <Flex direction="column" gap="5">
            {/* ── SMTP Host ── */}
            <Box>
              <FieldLabel
                label="SMTP Host *"
                hint="The hostname of your SMTP server"
              />
              <TextField.Root
                placeholder="email-smtp.ap-south-1.amazonaws.com"
                value={form.host}
                onChange={handleChange('host')}
                color={errors.host ? 'red' : undefined}
              >
                <TextField.Slot>
                  <MaterialIcon name="dns" size={16} color="var(--slate-9)" />
                </TextField.Slot>
              </TextField.Root>
              {errors.host && (
                <Text size="1" style={{ color: 'var(--red-a11)', marginTop: 'var(--space-1)', display: 'block' }}>
                  {errors.host}
                </Text>
              )}
            </Box>

            {/* ── Port ── */}
            <Box>
              <FieldLabel
                label="Port *"
                hint="Common ports: 25, 465, 587"
              />
              <TextField.Root
                type="number"
                placeholder="587"
                value={form.port === '' ? '' : String(form.port)}
                onChange={handleChange('port')}
                color={errors.port ? 'red' : undefined}
              >
                <TextField.Slot>
                  <MaterialIcon name="cell_tower" size={16} color="var(--slate-9)" />
                </TextField.Slot>
              </TextField.Root>
              {errors.port && (
                <Text size="1" style={{ color: 'var(--red-a11)', marginTop: 'var(--space-1)', display: 'block' }}>
                  {errors.port}
                </Text>
              )}
            </Box>

            {/* ── From Email Address ── */}
            <Box>
              <FieldLabel
                label="From Email Address *"
                hint="The email address that will appear as the sender"
              />
              <TextField.Root
                type="email"
                placeholder="noreply@yourcompany.com"
                value={form.fromEmail}
                onChange={handleChange('fromEmail')}
                color={errors.fromEmail ? 'red' : undefined}
              >
                <TextField.Slot>
                  <MaterialIcon name="mail" size={16} color="var(--slate-9)" />
                </TextField.Slot>
              </TextField.Root>
              {errors.fromEmail && (
                <Text size="1" style={{ color: 'var(--red-a11)', marginTop: 'var(--space-1)', display: 'block' }}>
                  {errors.fromEmail}
                </Text>
              )}
            </Box>

            {/* ── Username (Optional) ── */}
            <Box>
              <FieldLabel
                label="Username (Optional)"
                hint="Username for SMTP authentication (if required)"
              />
              <TextField.Root
                placeholder="Enter SMTP username"
                value={form.username}
                onChange={handleChange('username')}
              >
                <TextField.Slot>
                  <MaterialIcon name="manage_accounts" size={16} color="var(--slate-9)" />
                </TextField.Slot>
              </TextField.Root>
            </Box>

            {/* ── Password (Optional) ── */}
            <Box>
              <FieldLabel
                label="Password (Optional)"
                hint="Password for SMTP authentication (if required)"
              />
              <TextField.Root
                type={showPassword ? 'text' : 'password'}
                placeholder="••••••••••••••••••••"
                value={form.password}
                onChange={handleChange('password')}
              >
                <TextField.Slot>
                  <MaterialIcon name="lock" size={16} color="var(--slate-9)" />
                </TextField.Slot>
                <TextField.Slot side="right">
                  <Box
                    onClick={() => setShowPassword((v) => !v)}
                    style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                  >
                    <MaterialIcon
                      name={showPassword ? 'visibility_off' : 'visibility'}
                      size={16}
                      color="var(--slate-9)"
                    />
                  </Box>
                </TextField.Slot>
              </TextField.Root>
            </Box>
          </Flex>
        </Box>
      </Flex>
    </WorkspaceRightPanel>
  );
}
