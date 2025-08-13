export const TokenScopes = Object.freeze({
  SEND_MAIL: 'mail:send',
  FETCH_CONFIG: 'fetch:config',
  PASSWORD_RESET: 'password:reset',
  USER_LOOKUP: 'user:lookup',
  TOKEN_REFRESH: 'token:refresh',
  STORAGE_TOKEN: 'storage:token',
  CONVERSATION_CREATE: 'conversation:create',
} as const);

// Create a type for the TokenScopes keys
export type TokenScopes = (typeof TokenScopes)[keyof typeof TokenScopes];
