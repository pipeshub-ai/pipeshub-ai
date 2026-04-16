'use client';

import React from 'react';
import { Flex, Text, Select } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '../schema-form-field';
import { useConnectorsStore } from '../../store';
import { isNoneAuthType, isOAuthType } from '../../utils/auth-helpers';
import { DocumentationSection } from './documentation-section';
import { OAuthSection } from './oauth-section';
import { resolveAuthFields, formatAuthTypeName } from './helpers';
import type { AuthCardState } from '../../types';

// ========================================
// Component
// ========================================

export function AuthenticateTab() {
  const {
    connectorSchema,
    panelConnector,
    panelConnectorId,
    selectedAuthType,
    isAuthTypeImmutable: _isAuthTypeImmutable,
    formData,
    formErrors,
    conditionalDisplay,
    authState,
    setAuthFormValue,
    setSelectedAuthType,
  } = useConnectorsStore();

  if (!connectorSchema || !panelConnector) return null;

  const isCreateMode = !panelConnectorId;
  const schema = connectorSchema;
  const authConfig = schema.auth;
  const supportedAuthTypes = authConfig?.supportedAuthTypes ?? [];
  const showAuthTypeSelector = isCreateMode && supportedAuthTypes.length > 1;

  // Resolve current auth schema fields based on selected auth type
  const currentSchemaFields = resolveAuthFields(authConfig, selectedAuthType);

  // Documentation links
  const docLinks = schema.documentationLinks ?? [];

  // Determine if the auth card should be shown
  const showAuthCard = !isNoneAuthType(selectedAuthType);

  // Map authState to AuthCardState for the card
  const cardState: AuthCardState =
    authState === 'authenticating' ? 'empty' : (authState as AuthCardState);

  return (
    <Flex direction="column" gap="6" style={{ padding: 'var(--space-1) 0' }}>
      {/* ── A. Setup Documentation ── */}
      {docLinks.length > 0 && (
        <DocumentationSection
          links={docLinks}
          connectorIconPath={panelConnector.iconPath}
        />
      )}

      {/* ── Auth Type Selector (create mode only, multiple auth types) ── */}
      {showAuthTypeSelector && (
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Authentication Method
          </Text>
          <Select.Root
            value={selectedAuthType}
            onValueChange={setSelectedAuthType}
          >
            <Select.Trigger
              style={{ width: '100%', height: 32 }}
              placeholder="Select auth type..."
            />
            <Select.Content>
              {supportedAuthTypes.map((type) => (
                <Select.Item key={type} value={type}>
                  {formatAuthTypeName(type)}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        </Flex>
      )}

      {/* ── B. Auth Fields Section ── */}
      {currentSchemaFields.length > 0 && (
        <Flex direction="column" gap="5">
          <Flex direction="column" gap="1">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {formatAuthTypeName(selectedAuthType)} Credentials
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              Enter your {panelConnector.name} authentication details
            </Text>
          </Flex>

          {/* Render each auth field */}
          {currentSchemaFields.map((field) => {
            const isVisible =
              conditionalDisplay[field.name] !== undefined
                ? conditionalDisplay[field.name]
                : true;

            return (
              <SchemaFormField
                key={field.name}
                field={field}
                value={formData.auth[field.name]}
                onChange={setAuthFormValue}
                visible={isVisible}
                error={formErrors[field.name]}
                disabled={false}
              />
            );
          })}
        </Flex>
      )}

      {/* ── C. OAuth / Auth Card ── */}
      {showAuthCard && isOAuthType(selectedAuthType) && (
        <OAuthSection
          cardState={cardState}
          connectorName={panelConnector.name}
          isLoading={authState === 'authenticating'}
        />
      )}

      {/* ── For NONE auth type, show info message ── */}
      {isNoneAuthType(selectedAuthType) && (
        <Flex
          align="center"
          gap="2"
          style={{
            backgroundColor: 'var(--green-a3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3) var(--space-4)',
          }}
        >
          <MaterialIcon name="check_circle" size={16} color="var(--green-a11)" />
          <Text size="2" style={{ color: 'var(--green-a11)' }}>
            No authentication required for this connector
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
