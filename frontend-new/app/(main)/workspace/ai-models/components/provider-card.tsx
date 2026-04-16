'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AIModelProvider, ConfiguredModel } from '../types';
import { CAPABILITY_DISPLAY_NAMES, CAPABILITY_TO_MODEL_TYPE } from '../types';

interface ProviderCardProps {
  provider: AIModelProvider;
  configuredModels: ConfiguredModel[];
  onAdd: (provider: AIModelProvider, capability: string) => void;
  onEdit: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  onSetDefault: (modelType: string, modelKey: string) => void;
  onDelete: (modelType: string, modelKey: string, modelName: string) => void;
}

export function ProviderCard({
  provider,
  configuredModels,
  onAdd,
  onEdit,
  onSetDefault,
  onDelete,
}: ProviderCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  const modelsByCapability: Record<string, ConfiguredModel[]> = {};
  for (const cap of provider.capabilities) {
    const mt = CAPABILITY_TO_MODEL_TYPE[cap];
    if (!mt) continue;
    modelsByCapability[cap] = configuredModels.filter(
      (m) => m.modelType === mt && m.provider === provider.providerId
    );
  }

  const totalConfigured = Object.values(modelsByCapability).reduce(
    (sum, arr) => sum + arr.length,
    0
  );
  const isConfigured = totalConfigured > 0;

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 16,
        transition: 'background-color 150ms ease',
      }}
    >
      {/* Top: icon + name + description */}
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        <Flex align="center" gap="3">
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: 4,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={provider.iconPath}
              alt={provider.name}
              style={{ width: 20, height: 20, objectFit: 'contain' }}
            />
          </Flex>
          {provider.isPopular && (
            <Text
              size="1"
              style={{
                color: 'var(--accent-11)',
                backgroundColor: 'var(--accent-3)',
                padding: '1px 6px',
                borderRadius: 'var(--radius-1)',
                fontSize: 10,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              Popular
            </Text>
          )}
        </Flex>

        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Flex align="center" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {provider.name}
            </Text>
            {totalConfigured > 0 && (
              <Text
                size="1"
                style={{
                  color: 'var(--green-a11)',
                  backgroundColor: 'var(--green-a3)',
                  padding: '0 6px',
                  borderRadius: 'var(--radius-1)',
                  fontSize: 10,
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {totalConfigured} configured
              </Text>
            )}
          </Flex>
          <Text
            size="2"
            style={{
              color: 'var(--gray-11)',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {provider.description}
          </Text>
        </Flex>

        {/* Capability badges */}
        <Flex gap="2" wrap="wrap">
          {provider.capabilities.map((cap) => {
            const label = CAPABILITY_DISPLAY_NAMES[cap];
            if (!label) return null;
            const count = modelsByCapability[cap]?.length ?? 0;
            return (
              <Flex
                key={cap}
                align="center"
                gap="1"
                style={{
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-1)',
                  backgroundColor: count > 0 ? 'var(--green-a3)' : 'var(--gray-a3)',
                  flexShrink: 0,
                }}
              >
                <Text
                  size="1"
                  weight="medium"
                  style={{
                    color: count > 0 ? 'var(--green-a11)' : 'var(--gray-11)',
                    whiteSpace: 'nowrap',
                    fontSize: 11,
                  }}
                >
                  {label}{count > 0 ? ` (${count})` : ''}
                </Text>
              </Flex>
            );
          })}
        </Flex>
      </Flex>

      {/* Bottom: configured instances OR setup buttons */}
      {isConfigured ? (
        <ConfiguredSection
          provider={provider}
          modelsByCapability={modelsByCapability}
          onAdd={onAdd}
          onEdit={onEdit}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
        />
      ) : (
        <SetupButtons provider={provider} onAdd={onAdd} />
      )}
    </Flex>
  );
}

// ========================================
// Configured provider section — always shows instances
// ========================================

function ConfiguredSection({
  provider,
  modelsByCapability,
  onAdd,
  onEdit,
  onSetDefault,
  onDelete,
}: {
  provider: AIModelProvider;
  modelsByCapability: Record<string, ConfiguredModel[]>;
  onAdd: (p: AIModelProvider, cap: string) => void;
  onEdit: (p: AIModelProvider, cap: string, m: ConfiguredModel) => void;
  onSetDefault: (mt: string, mk: string) => void;
  onDelete: (mt: string, mk: string, name: string) => void;
}) {
  return (
    <Flex direction="column" gap="2" style={{ width: '100%' }}>
      {/* Model instances — always visible */}
      {provider.capabilities.map((cap) => {
        const mt = CAPABILITY_TO_MODEL_TYPE[cap];
        const models = modelsByCapability[cap] ?? [];
        if (models.length === 0) return null;
        const capLabel = CAPABILITY_DISPLAY_NAMES[cap] ?? cap;

        return (
          <Flex key={cap} direction="column" gap="1">
            {models.map((model) => {
              const modelName =
                model.modelFriendlyName ||
                (model.configuration?.model as string) ||
                model.provider;

              return (
                <ModelRow
                  key={model.modelKey}
                  modelName={modelName}
                  capLabel={capLabel}
                  isDefault={model.isDefault}
                  onEdit={(e) => {
                    e.stopPropagation();
                    onEdit(provider, cap, model);
                  }}
                  onSetDefault={(e) => {
                    e.stopPropagation();
                    if (mt) onSetDefault(mt, model.modelKey);
                  }}
                  onDelete={(e) => {
                    e.stopPropagation();
                    if (mt) onDelete(mt, model.modelKey, modelName);
                  }}
                />
              );
            })}
          </Flex>
        );
      })}

      {/* Add more buttons per capability */}
      <Flex gap="2" style={{ width: '100%', marginTop: 2 }}>
        {provider.capabilities.map((cap) => {
          const label = CAPABILITY_DISPLAY_NAMES[cap];
          if (!label) return null;
          return (
            <SetupButton
              key={cap}
              label={`+ ${label}`}
              onClick={() => onAdd(provider, cap)}
              flex
            />
          );
        })}
      </Flex>
    </Flex>
  );
}

// ========================================
// Setup buttons — for unconfigured providers
// ========================================

function SetupButtons({
  provider,
  onAdd,
}: {
  provider: AIModelProvider;
  onAdd: (p: AIModelProvider, cap: string) => void;
}) {
  if (provider.capabilities.length === 1) {
    const cap = provider.capabilities[0];
    const label = CAPABILITY_DISPLAY_NAMES[cap] ?? cap;
    return <SetupButton label={`+ ${label}`} onClick={() => onAdd(provider, cap)} />;
  }

  return (
    <Flex gap="2" style={{ width: '100%' }}>
      {provider.capabilities.map((cap) => {
        const label = CAPABILITY_DISPLAY_NAMES[cap];
        if (!label) return null;
        return (
          <SetupButton
            key={cap}
            label={`+ ${label}`}
            onClick={() => onAdd(provider, cap)}
            flex
          />
        );
      })}
    </Flex>
  );
}

function SetupButton({
  label,
  onClick,
  flex,
}: {
  label: string;
  onClick: () => void;
  flex?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
        flex: flex ? 1 : undefined,
        width: flex ? undefined : '100%',
        height: 32,
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--gray-a4)' : 'var(--gray-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <span
        style={{
          fontSize: 13,
          fontWeight: 500,
          lineHeight: '20px',
          color: 'var(--gray-11)',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
    </button>
  );
}

// ========================================
// Model instance row
// ========================================

function ModelRow({
  modelName,
  capLabel,
  isDefault,
  onEdit,
  onSetDefault,
  onDelete,
}: {
  modelName: string;
  capLabel: string;
  isDefault: boolean;
  onEdit: (e: React.MouseEvent) => void;
  onSetDefault: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      justify="between"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: '5px 8px',
        borderRadius: 'var(--radius-1)',
        backgroundColor: 'var(--gray-a2)',
        transition: 'background-color 100ms ease',
        minHeight: 32,
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--gray-12)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {modelName}
        </Text>
        <Text
          size="1"
          style={{
            color: 'var(--gray-9)',
            flexShrink: 0,
            fontSize: 10,
            backgroundColor: 'var(--gray-a3)',
            padding: '0 4px',
            borderRadius: 'var(--radius-1)',
          }}
        >
          {capLabel}
        </Text>
        {isDefault && (
          <Text
            size="1"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-3)',
              padding: '0 4px',
              borderRadius: 'var(--radius-1)',
              fontSize: 9,
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            Default
          </Text>
        )}
      </Flex>

      <Flex
        align="center"
        gap="0"
        style={{
          opacity: isHovered ? 1 : 0,
          transition: 'opacity 150ms ease',
          flexShrink: 0,
        }}
      >
        <IconBtn icon="edit" title="Edit" onClick={onEdit} />
        {!isDefault && <IconBtn icon="star_outline" title="Set default" onClick={onSetDefault} />}
        <IconBtn icon="delete" title="Delete" onClick={onDelete} color="var(--red-9)" />
      </Flex>
    </Flex>
  );
}

function IconBtn({
  icon,
  title,
  onClick,
  color,
}: {
  icon: string;
  title: string;
  onClick: (e: React.MouseEvent) => void;
  color?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        appearance: 'none',
        margin: 0,
        padding: 2,
        border: 'none',
        outline: 'none',
        background: 'transparent',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <MaterialIcon name={icon} size={14} color={color ?? 'var(--gray-11)'} />
    </button>
  );
}
