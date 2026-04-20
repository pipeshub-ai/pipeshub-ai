'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text, Button, TextField, Spinner } from '@radix-ui/themes';
import { InfoBanner } from './info-banner';
import { useOnboardingStore } from '../store';
import { getStorageConfig, saveStorageConfig } from '../api';
import type { StorageFormData, StorageProviderType, OnboardingStepId } from '../types';

const STORAGE_PROVIDERS: { value: StorageProviderType; label: string }[] = [
  { value: 'local', label: 'Local (System Default)' },
  { value: 's3', label: 'Amazon S3' },
  // Azure Blob temporarily hidden from onboarding UI — restore when ready:
  // { value: 'azureBlob', label: 'Azure Blob Storage' },
];

const selectStyle: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  color: 'var(--gray-12)',
  border: '1px solid var(--gray-5)',
  borderRadius: 'var(--radius-2)',
  padding: '0 8px',
  height: '36px',
  fontSize: '14px',
  width: '100%',
  outline: 'none',
  appearance: 'auto',
};

interface StepStorageProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepStorage({
  onSuccess,
  systemStepIndex,
  totalSystemSteps,
}: StepStorageProps) {
  const { storage, setStorage, markStepCompleted, unmarkStepCompleted, submitting, setSubmitting, setSubmitStatus } =
    useOnboardingStore();

  const [form, setForm] = useState<StorageFormData>({
    providerType: storage.providerType || 'local',
  });

  const [showSecret, setShowSecret] = useState(false);
  const [showAccountKey, setShowAccountKey] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const isDirtyRef = useRef(false);

  // Mark step as pre-completed when GET data is available (and user hasn't edited)
  useEffect(() => {
    if (!loadingConfig && !isDirtyRef.current) {
      const s3Complete =
        form.providerType === 's3' &&
        (form.s3AccessKeyId?.trim() ?? '') !== '' &&
        (form.s3SecretAccessKey?.trim() ?? '') !== '' &&
        (form.s3Region?.trim() ?? '') !== '' &&
        (form.s3BucketName?.trim() ?? '') !== '';
      const azureComplete =
        form.providerType === 'azureBlob' &&
        (form.accountName?.trim() ?? '') !== '' &&
        (form.accountKey?.trim() ?? '') !== '' &&
        (form.containerName?.trim() ?? '') !== '';
      const preComplete =
        form.providerType === 'local' || s3Complete || azureComplete;
      if (preComplete) {
        markStepCompleted('storage');
      }
    }
  }, [form, loadingConfig]);

  useEffect(() => {
    getStorageConfig()
      .then((config) => {
        setForm((prev) => ({
          ...prev,
          providerType: config.storageType || prev.providerType,
          s3AccessKeyId: config.s3AccessKeyId,
          s3SecretAccessKey: config.s3SecretAccessKey,
          s3Region: config.s3Region,
          s3BucketName: config.s3BucketName,
          accountName: config.accountName,
          accountKey: config.accountKey,
          containerName: config.containerName,
          endpointProtocol: config.endpointProtocol,
          endpointSuffix: config.endpointSuffix,
          mountName: config.mountName,
          baseUrl: config.baseUrl,
        }));
      })
      .catch(() => { /* use defaults */ })
      .finally(() => setLoadingConfig(false));
  }, []);

  const handleChange = <K extends keyof StorageFormData>(field: K, value: StorageFormData[K]) => {
    isDirtyRef.current = true;
    unmarkStepCompleted('storage');
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitStatus('loading');
    setStorage(form);

    try {
      await saveStorageConfig(form);
      setSubmitStatus('success');
      onSuccess(null);
    } catch {
      setSubmitStatus('error');
    } finally {
      setSubmitting(false);
    }
  };

  const isS3 = form.providerType === 's3';
  const isAzure = form.providerType === 'azureBlob';
  const isLocal = form.providerType === 'local';

  // Client-side validation: local is always valid; S3/Azure require their key fields
  const isFormValid =
    isLocal ||
    (isS3 &&
      (form.s3AccessKeyId?.trim() ?? '') !== '' &&
      (form.s3SecretAccessKey?.trim() ?? '') !== '' &&
      (form.s3Region?.trim() ?? '') !== '' &&
      (form.s3BucketName?.trim() ?? '') !== '') ||
    (isAzure &&
      (form.accountName?.trim() ?? '') !== '' &&
      (form.accountKey?.trim() ?? '') !== '' &&
      (form.containerName?.trim() ?? '') !== '');

  if (loadingConfig) {
    return (
      <Box
        style={{
          backgroundColor: 'var(--gray-2)',
          border: '1px solid var(--gray-4)',
          borderRadius: 'var(--radius-3)',
          padding: '24px',
          width: '576px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '200px',
        }}
      >
        <Spinner size="3" />
      </Box>
    );
  }

  return (
    <Box
      style={{
        backgroundColor: 'var(--gray-2)',
        border: '1px solid var(--gray-4)',
        borderRadius: 'var(--radius-3)',
        width: '576px',
        maxHeight: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Fixed Sub-header */}
      <Box
        style={{
          flexShrink: 0,
          padding: '24px 24px 16px',
          borderBottom: '1px solid var(--gray-4)',
        }}
      >
        <Text
          as="div"
          size="1"
          style={{ color: 'var(--gray-9)', marginBottom: '4px', letterSpacing: '0.02em' }}
        >
          System Configuration
        </Text>
        <Text
          as="div"
          size="4"
          weight="bold"
          style={{ color: 'var(--gray-12)' }}
        >
          Step {systemStepIndex}/{totalSystemSteps}: Configure Storage*
        </Text>
      </Box>

      {/* Scrollable fields */}
      <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
        <Flex direction="column" gap="6">
        <InfoBanner message="Choose your preferred storage solution. Local storage is used by default if skipped." />

        {/* Provider Type */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Provider Type*
          </Text>
          <select
            value={form.providerType}
            onChange={(e) => handleChange('providerType', e.target.value as StorageProviderType)}
            disabled={submitting}
            style={selectStyle}
          >
            {STORAGE_PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </Flex>

        {/* S3 fields */}
        {isS3 && (
          <>
            {/* Access Key + Secret Key side by side */}
            <Flex gap="3">
              <Flex direction="column" gap="1" style={{ flex: 1 }}>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Access Key*</Text>
                <TextField.Root
                  placeholder="Enter Access Key"
                  value={form.s3AccessKeyId ?? ''}
                  onChange={(e) => handleChange('s3AccessKeyId', e.target.value)}
                  disabled={submitting}
                />
              </Flex>
              <Flex direction="column" gap="1" style={{ flex: 1 }}>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Secret Key*</Text>
                <TextField.Root
                  type={showSecret ? 'text' : 'password'}
                  placeholder="Enter Secret Key"
                  value={form.s3SecretAccessKey ?? ''}
                  onChange={(e) => handleChange('s3SecretAccessKey', e.target.value)}
                  disabled={submitting}
                >
                  <TextField.Slot side="right">
                    <button
                      type="button"
                      onClick={() => setShowSecret((v) => !v)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '0', display: 'flex', alignItems: 'center' }}
                    >
                      <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                        {showSecret ? 'visibility_off' : 'visibility'}
                      </span>
                    </button>
                  </TextField.Slot>
                </TextField.Root>
              </Flex>
            </Flex>
            {/* Bucket full width */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Bucket*</Text>
              <TextField.Root
                placeholder="Enter Bucket"
                value={form.s3BucketName ?? ''}
                onChange={(e) => handleChange('s3BucketName', e.target.value)}
                disabled={submitting}
              />
            </Flex>
            {/* Region dropdown full width */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Region*</Text>
              <select
                value={form.s3Region ?? ''}
                onChange={(e) => handleChange('s3Region', e.target.value)}
                disabled={submitting}
                style={selectStyle}
              >
                <option value="">Select Region</option>
                <option value="us-east-1">US East (N. Virginia)</option>
                <option value="us-east-2">US East (Ohio)</option>
                <option value="us-west-1">US West (N. California)</option>
                <option value="us-west-2">US West (Oregon)</option>
                <option value="eu-west-1">EU (Ireland)</option>
                <option value="eu-west-2">EU (London)</option>
                <option value="eu-central-1">EU (Frankfurt)</option>
                <option value="ap-northeast-1">AP Northeast (Tokyo)</option>
                <option value="ap-northeast-2">AP Northeast (Seoul)</option>
                <option value="ap-southeast-1">AP Southeast (Singapore)</option>
                <option value="ap-southeast-2">AP Southeast (Sydney)</option>
                <option value="ap-south-1">AP South (Mumbai)</option>
                <option value="sa-east-1">SA East (São Paulo)</option>
                <option value="ca-central-1">Canada (Central)</option>
              </select>
            </Flex>
          </>
        )}

        {/* Azure Blob fields */}
        {isAzure && (
          <>
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Account Name*</Text>
              <TextField.Root
                placeholder="Enter Account Name"
                value={form.accountName ?? ''}
                onChange={(e) => handleChange('accountName', e.target.value)}
                disabled={submitting}
              />
            </Flex>
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Account Key*</Text>
              <TextField.Root
                type={showAccountKey ? 'text' : 'password'}
                placeholder="Enter Account Key"
                value={form.accountKey ?? ''}
                onChange={(e) => handleChange('accountKey', e.target.value)}
                disabled={submitting}
              >
                <TextField.Slot side="right">
                  <button
                    type="button"
                    onClick={() => setShowAccountKey((v) => !v)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '0', display: 'flex', alignItems: 'center' }}
                  >
                    <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                      {showAccountKey ? 'visibility_off' : 'visibility'}
                    </span>
                  </button>
                </TextField.Slot>
              </TextField.Root>
            </Flex>
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Container*</Text>
              <TextField.Root
                placeholder="Enter Container"
                value={form.containerName ?? ''}
                onChange={(e) => handleChange('containerName', e.target.value)}
                disabled={submitting}
              />
            </Flex>
          </>
        )}

        {/* Local optional fields */}
        {isLocal && (
          <>
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Mount Name (optional)</Text>
              <TextField.Root
                placeholder="eg: uploads"
                value={form.mountName ?? ''}
                onChange={(e) => handleChange('mountName', e.target.value)}
                disabled={submitting}
              />
            </Flex>
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Base URL (optional)</Text>
              <TextField.Root
                placeholder="eg: http://localhost:3000/files"
                value={form.baseUrl ?? ''}
                onChange={(e) => handleChange('baseUrl', e.target.value)}
                disabled={submitting}
              />
            </Flex>
          </>
        )}

        </Flex>
      </Box>

      {/* Fixed footer: Save button */}
      <Box style={{ flexShrink: 0, padding: '0 24px 24px' }}>
        <Button
          onClick={handleSubmit}
          disabled={submitting || !isFormValid}
          style={{
            width: '100%',
            backgroundColor: submitting || !isFormValid ? 'var(--gray-4)' : 'var(--accent-9)',
            color: submitting || !isFormValid ? 'var(--gray-9)' : 'white',
            cursor: submitting || !isFormValid ? 'not-allowed' : 'pointer',
            height: '40px',
            opacity: 1,
          }}
        >
          {submitting ? (
            <Flex align="center" gap="2">
              <Spinner size="1" />
              Saving…
            </Flex>
          ) : (
            'Save'
          )}
        </Button>
      </Box>
    </Box>
  );
}
