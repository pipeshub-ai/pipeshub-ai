'use client';

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { XmlUploadFieldDef } from '../../constants';

// ── SAML metadata XML parser ──────────────────────────────────

function parseSamlMetadata(xmlText: string): Record<string, string> {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, 'application/xml');
  const result: Record<string, string> = {};

  const parseError = doc.querySelector('parsererror');
  if (parseError) return result;

  // Entity ID
  const entityDescriptor = doc.querySelector('EntityDescriptor');
  if (entityDescriptor) {
    const entityId = entityDescriptor.getAttribute('entityID');
    if (entityId) result.entityId = entityId;
  }

  // SSO entry point — prefer HTTP-POST, fall back to HTTP-Redirect
  const ssoPost = doc.querySelector(
    'SingleSignOnService[Binding$="HTTP-POST"]',
  );
  const ssoRedirect = doc.querySelector(
    'SingleSignOnService[Binding$="HTTP-Redirect"]',
  );
  const ssoEl = ssoPost ?? ssoRedirect;
  if (ssoEl) {
    const location = ssoEl.getAttribute('Location');
    if (location) result.entryPoint = location;
  }

  // Logout URL
  const sloEl = doc.querySelector('SingleLogoutService[Binding$="HTTP-POST"], SingleLogoutService[Binding$="HTTP-Redirect"]');
  if (sloEl) {
    const location = sloEl.getAttribute('Location');
    if (location) result.logoutUrl = location;
  }

  // X.509 certificate (first one found under IDPSSODescriptor)
  const idpDescriptor = doc.querySelector('IDPSSODescriptor');
  if (idpDescriptor) {
    const certEl = idpDescriptor.querySelector('X509Certificate');
    if (certEl) {
      const raw = certEl.textContent?.replace(/\s+/g, '').trim() ?? '';
      if (raw) {
        result.certificate = `-----BEGIN CERTIFICATE-----\n${raw}\n-----END CERTIFICATE-----`;
      }
    }
  }

  return result;
}

// ── Component ─────────────────────────────────────────────────

interface XmlUploadFieldProps {
  field: XmlUploadFieldDef;
  onPopulate: (values: Record<string, string>) => void;
}

export function XmlUploadField({ field, onPopulate }: XmlUploadFieldProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setError(null);
    setFileName(file.name);

    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result;
      if (typeof text !== 'string') return;
      const parsed = parseSamlMetadata(text);
      if (Object.keys(parsed).length === 0) {
        setFileName(null);
        setError(t('workspace.authentication.xmlUpload.parseError'));
        return;
      }
      onPopulate(parsed);
    };
    reader.onerror = () => {
      setFileName(null);
      setError(t('workspace.authentication.xmlUpload.readError'));
    };
    reader.readAsText(file);

    // Reset so the same file can be re-selected after clearing
    e.target.value = '';
  };

  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} />

      <Flex align="center" gap="2">
        <Button
          variant="outline"
          size="2"
          type="button"
          onClick={() => inputRef.current?.click()}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="upload_file" size={16} color="var(--slate-11)" />
          Choose XML File
        </Button>

        {fileName && (
          <Flex align="center" gap="1">
            <MaterialIcon name="description" size={14} color="var(--slate-10)" />
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {fileName}
            </Text>
            <Box
              style={{ cursor: 'pointer', lineHeight: 0 }}
              onClick={() => {
                setFileName(null);
                setError(null);
              }}
            >
              <MaterialIcon name="close" size={14} color="var(--slate-9)" />
            </Box>
          </Flex>
        )}
      </Flex>

      <input
        ref={inputRef}
        type="file"
        accept=".xml,application/xml,text/xml"
        hidden
        onChange={handleChange}
      />

      {error ? (
        <Text size="1" style={{ color: 'var(--red-9)' }}>
          {error}
        </Text>
      ) : (
        field.helperText && (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {field.helperText}
          </Text>
        )
      )}
    </Flex>
  );
}
