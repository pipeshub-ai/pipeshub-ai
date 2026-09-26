export const TokenScopes = Object.freeze({
  SEND_MAIL: 'mail:send',
  FETCH_CONFIG: 'fetch:config',
  PASSWORD_RESET: 'password:reset',
  USER_LOOKUP: 'user:lookup',
  TOKEN_REFRESH: 'token:refresh',
  STORAGE_TOKEN: 'storage:token',
  CONVERSATION_CREATE: 'conversation:create',
  CONVERSATION_PERMISSIONS: 'conversation:permissions',
  VALIDATE_EMAIL: 'email:validate',
  ORG_EMAIL_VERIFY: 'org:email:verify',
  EMAIL_VERIFIED: 'email:verified',
  DESKTOP_COMMAND: 'desktop:command',
} as const);

// Create a type for the TokenScopes keys
export type TokenScopes = (typeof TokenScopes)[keyof typeof TokenScopes];

// Scopes on tokens held by end users; these are signed with the derived
// user-action key (libs/utils/jwtKeys.ts), never the raw scoped secret.
export const USER_ACTION_TOKEN_SCOPES: ReadonlySet<TokenScopes> =
  new Set<TokenScopes>([
    TokenScopes.PASSWORD_RESET,
    TokenScopes.VALIDATE_EMAIL,
    TokenScopes.TOKEN_REFRESH,
    TokenScopes.ORG_EMAIL_VERIFY,
    TokenScopes.EMAIL_VERIFIED,
  ]);

export const isUserActionScope = (scope: string): boolean =>
  (USER_ACTION_TOKEN_SCOPES as ReadonlySet<string>).has(scope);
