import type { ReactNode } from 'react';
import type { AgentDetail } from '../types';

export interface AgentNameFieldOptions {
  loadedAgent: AgentDetail | null;
  agentName: string;
  /** The user may change the agent's settings. */
  canEdit: boolean;
}

/** Extra settings shown under the agent name and saved with the agent. */
export interface AgentNameField {
  /** Unsaved changes. */
  dirty: boolean;
  /** Fields added to the create/update payload. */
  payloadFields(): Record<string, unknown>;
  /** Options for the create/update request. */
  requestOptions(): { suppressErrorToast?: boolean } | undefined;
  /** `true` when a save failure was shown by the field itself. */
  handleSaveError(error: unknown): boolean;
  nameFieldExtra: ReactNode;
}

export interface AgentBuilderExtension {
  useNameField(opts: AgentNameFieldOptions): AgentNameField;
}

const NO_NAME_FIELD: AgentNameField = {
  dirty: false,
  payloadFields: () => ({}),
  requestOptions: () => undefined,
  handleSaveError: () => false,
  nameFieldExtra: null,
};

export const defaultAgentBuilderExtension: AgentBuilderExtension = {
  useNameField: () => NO_NAME_FIELD,
};
