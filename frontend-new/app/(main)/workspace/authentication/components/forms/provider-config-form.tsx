'use client';

import React, {
  useEffect,
  useState,
  useImperativeHandle,
  forwardRef,
  useCallback,
} from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { PROVIDER_CONFIGS } from '../../constants';
import type { ConfigurableMethod } from '../../types';
import type { FieldDef } from '../../constants';
import { ReadonlyField } from './readonly-field';
import { InputField } from './input-field';
import { PasswordInputField } from './password-input-field';
import { TextareaField } from './textarea-field';
import { JitField } from './jit-field';

// ============================================================
// Public ref type
// ============================================================

export interface ProviderConfigFormRef {
  submit: () => Promise<boolean>;
}

// ============================================================
// Props
// ============================================================

interface ProviderConfigFormProps {
  method: ConfigurableMethod;
  onValidChange: (valid: boolean) => void;
}

// ============================================================
// Unified form component
// ============================================================

const ProviderConfigForm = forwardRef<ProviderConfigFormRef, ProviderConfigFormProps>(
  ({ method, onValidChange }, ref) => {
    const config = PROVIDER_CONFIGS[method];
    const [values, setValues] = useState<Record<string, string | boolean>>({});
    const [isLoading, setIsLoading] = useState(true);

    // ── Derived required keys ─────────────────────────────
    const requiredKeys = config.fields
      .filter(
        (f): f is Extract<FieldDef, { required?: boolean }> =>
          (f.type === 'text' || f.type === 'password' || f.type === 'textarea') && !!f.required,
      )
      .map((f) => f.key);

    // ── Load initial values ───────────────────────────────
    useEffect(() => {
      let cancelled = false;
      setIsLoading(true);
      config.loadValues().then((initial) => {
        if (!cancelled) {
          setValues(initial);
          setIsLoading(false);
        }
      });
      return () => { cancelled = true; };
    // Re-run if method changes while panel is open (edge case)
    }, [method]);

    // ── Validation ────────────────────────────────────────
    useEffect(() => {
      if (isLoading) return;
      const valid = requiredKeys.every(
        (k) => String(values[k] ?? '').trim().length > 0,
      );
      onValidChange(valid);
    }, [values, isLoading]);

    // ── Value helpers ─────────────────────────────────────
    const setString = useCallback((key: string, val: string) => {
      setValues((prev) => ({ ...prev, [key]: val }));
    }, []);

    const setBool = useCallback((key: string, val: boolean) => {
      setValues((prev) => ({ ...prev, [key]: val }));
    }, []);

    // ── Imperative submit ─────────────────────────────────
    useImperativeHandle(ref, () => ({
      async submit() {
        const missingRequired = requiredKeys.some(
          (k) => String(values[k] ?? '').trim().length === 0,
        );
        if (missingRequired) return false;
        try {
          await config.saveValues(values);
          return true;
        } catch {
          return false;
        }
      },
    }));

    // ── Loading state ─────────────────────────────────────
    if (isLoading) {
      return (
        <Flex align="center" justify="center" style={{ padding: '32px 0' }}>
          <LottieLoader variant="loader" size={48} />
        </Flex>
      );
    }

    // ── Field rendering ───────────────────────────────────
    return (
      <Flex direction="column" gap="4">
        {config.fields.map((field) => {
          if (field.type === 'readonly') {
            const warned = field.warningKey ? Boolean(values[field.warningKey]) : false;
            return (
              <ReadonlyField
                key={field.key}
                field={field}
                value={String(values[field.key] ?? '')}
                warned={warned}
              />
            );
          }

          if (field.type === 'text') {
            return (
              <InputField
                key={field.key}
                field={field}
                value={String(values[field.key] ?? '')}
                onChange={(val) => setString(field.key, val)}
              />
            );
          }

          if (field.type === 'password') {
            return (
              <PasswordInputField
                key={field.key}
                field={field}
                value={String(values[field.key] ?? '')}
                onChange={(val) => setString(field.key, val)}
              />
            );
          }

          if (field.type === 'textarea') {
            return (
              <TextareaField
                key={field.key}
                field={field}
                value={String(values[field.key] ?? '')}
                onChange={(val) => setString(field.key, val)}
              />
            );
          }

          if (field.type === 'jit') {
            return (
              <JitField
                key="jit"
                providerName={field.providerName}
                checked={Boolean(values.enableJit)}
                onCheckedChange={(val) => setBool('enableJit', val)}
              />
            );
          }

          return null;
        })}
      </Flex>
    );
  },
);

ProviderConfigForm.displayName = 'ProviderConfigForm';
export default ProviderConfigForm;
