'use client';

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { XmlUploadFieldDef } from '../../constants';

// ── SAML metadata XML parser ──────────────────────────────────

type SamlErrorKind = 'malformed' | 'not-saml' | 'invalid-url' | 'invalid-cert';

interface SamlParseResult {
  values: Record<string, string> | null;
  errorKind: SamlErrorKind | null;
}

const BASE64_RE = /^[A-Za-z0-9+/]+=*$/;

function isValidHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function parseSamlMetadata(xmlText: string): SamlParseResult {
  let doc: Document;
  try {
    const parser = new DOMParser();
    doc = parser.parseFromString(xmlText, 'application/xml');
  } catch {
    return { values: null, errorKind: 'malformed' };
  }

  if (doc.querySelector('parsererror')) {
    return { values: null, errorKind: 'malformed' };
  }

  // Confirm this is actually SAML metadata before extracting any fields
  const entityDescriptor = doc.querySelector('EntityDescriptor');
  if (!entityDescriptor) {
    return { values: null, errorKind: 'not-saml' };
  }

  const result: Record<string, string> = {};

  // Entity ID
  const entityId = entityDescriptor.getAttribute('entityID')?.trim();
  if (entityId) result.entityId = entityId;

  // SSO entry point — prefer HTTP-POST, fall back to HTTP-Redirect
  try {
    const ssoPost = doc.querySelector('SingleSignOnService[Binding$="HTTP-POST"]');
    const ssoRedirect = doc.querySelector('SingleSignOnService[Binding$="HTTP-Redirect"]');
    const ssoEl = ssoPost ?? ssoRedirect;
    if (ssoEl) {
      const location = ssoEl.getAttribute('Location') ?? '';
      if (location && !isValidHttpUrl(location)) {
        return { values: null, errorKind: 'invalid-url' };
      }
      if (location) result.entryPoint = location;
    }
  } catch {
    return { values: null, errorKind: 'malformed' };
  }

  // Logout URL
  try {
    const sloEl = doc.querySelector(
      'SingleLogoutService[Binding$="HTTP-POST"], SingleLogoutService[Binding$="HTTP-Redirect"]',
    );
    if (sloEl) {
      const location = sloEl.getAttribute('Location') ?? '';
      if (location && !isValidHttpUrl(location)) {
        return { values: null, errorKind: 'invalid-url' };
      }
      if (location) result.logoutUrl = location;
    }
  } catch {
    return { values: null, errorKind: 'malformed' };
  }

  // X.509 certificate (first one found under IDPSSODescriptor)
  try {
    const idpDescriptor = doc.querySelector('IDPSSODescriptor');
    if (idpDescriptor) {
      const certEl = idpDescriptor.querySelector('X509Certificate');
      if (certEl) {
        const raw = certEl.textContent?.replace(/\s+/g, '').trim() ?? '';
        if (raw) {
          if (!BASE64_RE.test(raw)) {
            return { values: null, errorKind: 'invalid-cert' };
          }
          result.certificate = `-----BEGIN CERTIFICATE-----\n${raw}\n-----END CERTIFICATE-----`;
        }
      }
    }
  } catch {
    return { values: null, errorKind: 'malformed' };
  }

  return { values: result, errorKind: null };
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
      const result = parseSamlMetadata(text);
      if (result.errorKind) {
        setFileName(null);
        const errorKey = {
          'malformed': 'workspace.authentication.xmlUpload.parseError',
          'not-saml': 'workspace.authentication.xmlUpload.notSamlError',
          'invalid-url': 'workspace.authentication.xmlUpload.invalidUrlError',
          'invalid-cert': 'workspace.authentication.xmlUpload.invalidCertError',
        }[result.errorKind];
        setError(t(errorKey));
        return;
      }
      if (result.values) onPopulate(result.values);
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
