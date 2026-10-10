'use client';

import React, { useEffect, useId, useState } from 'react';
import { Button, Flex, IconButton, Text, TextField, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  normalizeHandleInput,
  validateAgentHandle,
  type AgentHandleServerError,
} from '../agent-handle-utils';

/**
 * The agent's @mention handle under its name: read-only text, with an inline
 * editor for anyone who may edit the agent. A handle the user has not chosen
 * is the server's own derivation from the name, shown as a preview.
 */
export function AgentHandleField(props: {
  /** Chosen, stored or (when `isDerived`) previewed handle, without the `@`. */
  value: string;
  /** True while the value is the server's derivation from the name rather than a stored or chosen handle. */
  isDerived: boolean;
  canEdit: boolean;
  onChange: (handle: string) => void;
  serverError?: AgentHandleServerError | null;
}) {
  const { value, isDerived, canEdit, onChange, serverError = null } = props;
  const { t } = useTranslation();
  const inputId = useId();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  const problem = touched ? validateAgentHandle(draft) : null;
  const clientError =
    problem === 'reserved'
      ? t('agentBuilder.handle.reserved', { handle: normalizeHandleInput(draft) })
      : problem === 'invalid'
        ? t('agentBuilder.handle.invalid')
        : null;

  const serverMessage = serverError
    ? serverError.code === 'HANDLE_TAKEN'
      ? serverError.suggestion
        ? t('agentBuilder.handle.takenSuggestion', { handle: serverError.handle, suggestion: serverError.suggestion })
        : t('agentBuilder.handle.taken', { handle: serverError.handle })
      : serverError.code === 'HANDLE_RESERVED'
        ? t('agentBuilder.handle.reserved', { handle: serverError.handle })
        : t('agentBuilder.handle.invalid')
    : null;

  const startEditing = () => {
    setDraft(value);
    setTouched(false);
    setEditing(true);
  };

  const commit = () => {
    setTouched(true);
    if (validateAgentHandle(draft)) return;
    onChange(normalizeHandleInput(draft));
    setEditing(false);
  };

  const cancel = () => {
    setEditing(false);
    setTouched(false);
    setDraft(value);
  };

  return (
    <Flex direction="column" gap="1" mt="1" data-testid="agent-handle-field">
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text
          as="label"
          htmlFor={editing ? inputId : undefined}
          size="1"
          style={{ color: 'var(--olive-10)', flexShrink: 0 }}
        >
          {t('agentBuilder.handle.label')}
        </Text>
        {editing ? (
          <>
            <TextField.Root
              id={inputId}
              value={draft}
              size="1"
              autoFocus
              placeholder={t('agentBuilder.handle.placeholder')}
              aria-label={t('agentBuilder.handle.inputAria')}
              aria-invalid={clientError ? true : undefined}
              color={clientError ? 'red' : undefined}
              onChange={(e) => {
                setDraft(e.target.value);
                setTouched(true);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commit();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancel();
                }
              }}
              style={{ width: 168 }}
            >
              <TextField.Slot side="left">
                <Text size="1" style={{ color: 'var(--olive-11)' }}>@</Text>
              </TextField.Slot>
            </TextField.Root>
            <IconButton
              type="button"
              size="1"
              variant="soft"
              color="gray"
              aria-label={t('agentBuilder.handle.done')}
              disabled={Boolean(validateAgentHandle(draft))}
              onClick={commit}
            >
              <MaterialIcon name="check" size={14} />
            </IconButton>
            <IconButton
              type="button"
              size="1"
              variant="ghost"
              color="gray"
              aria-label={t('agentBuilder.handle.cancel')}
              onClick={cancel}
            >
              <MaterialIcon name="close" size={14} />
            </IconButton>
          </>
        ) : (
          <>
            <Tooltip content={isDerived ? t('agentBuilder.handle.derivedHint') : t('agentBuilder.handle.hint')}>
              <Text
                size="1"
                data-testid="agent-handle-value"
                style={{
                  color: 'var(--olive-12)',
                  fontFamily: 'var(--code-font-family)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  maxWidth: 200,
                  fontStyle: isDerived ? 'italic' : 'normal',
                }}
              >
                @{value}
              </Text>
            </Tooltip>
            {canEdit ? (
              <IconButton
                type="button"
                size="1"
                variant="ghost"
                color="gray"
                aria-label={t('agentBuilder.handle.edit')}
                onClick={startEditing}
              >
                <MaterialIcon name="edit" size={14} />
              </IconButton>
            ) : null}
          </>
        )}
      </Flex>
      {editing && clientError ? (
        <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {clientError}
        </Text>
      ) : null}
      {!editing && serverMessage ? (
        <Flex align="center" gap="2" wrap="wrap">
          <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
            {serverMessage}
          </Text>
          {serverError?.suggestion && canEdit ? (
            <Button
              type="button"
              size="1"
              variant="soft"
              onClick={() => onChange(serverError.suggestion as string)}
            >
              {t('agentBuilder.handle.useSuggestion', { suggestion: serverError.suggestion })}
            </Button>
          ) : null}
        </Flex>
      ) : null}
    </Flex>
  );
}
