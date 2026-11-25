// ========================================
// Bot page types
// ========================================

/** Supported bot platform types */
export type BotType = 'slack' | 'discord' | 'telegram' | 'github';

/** Metadata for a bot type shown in the type selector */
export interface BotTypeInfo {
  type: BotType;
  label: string;
  icon: string;
  enabled: boolean;
}

/** Panel view state */
export type PanelView = 'type-selector' | 'slack-form';

// ========================================
// Slack bot types
// ========================================

export interface SlackBotConfig {
  id: string;
  name: string;
  botToken: string;
  signingSecret: string;
  agentId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface SlackBotConfigPayload {
  name: string;
  botToken: string;
  signingSecret: string;
  agentId?: string;
}

// ========================================
// Shared types
// ========================================

export interface AgentOption {
  id: string;
  name: string;
}

/** A Slack person and the PipesHub account they linked by signing in; `active` is false once that account is gone. */
export interface SlackIdentityLink {
  teamId: string;
  slackUserId: string;
  slackName: string | null;
  userId: string;
  email: string | null;
  fullName: string | null;
  linkedAt: string;
  active: boolean;
}
