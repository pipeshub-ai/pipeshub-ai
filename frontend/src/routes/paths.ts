// ----------------------------------------------------------------------

const ROOTS = {
  AUTH: '/auth',
  AUTH_DEMO: '/auth-demo',
  DASHBOARD: '/',
};

// ----------------------------------------------------------------------

export const paths = {
  maintenance: '/maintenance',
  page403: '/error/403',
  page404: '/error/404',
  page500: '/error/500',
  // AUTH
  auth: {
    jwt: {
      signIn: `${ROOTS.AUTH}/sign-in`,
      signUp: `${ROOTS.AUTH}/sign-up`,
      resetPassword: `${ROOTS.AUTH}/reset-password`,
    },
  },
  authDemo: {
    split: {
      signIn: `${ROOTS.AUTH_DEMO}/split/sign-in`,
      signUp: `${ROOTS.AUTH_DEMO}/split/sign-up`,
      resetPassword: `${ROOTS.AUTH_DEMO}/split/reset-password`,
      updatePassword: `${ROOTS.AUTH_DEMO}/split/update-password`,
      verify: `${ROOTS.AUTH_DEMO}/split/verify`,
    },
    centered: {
      signIn: `${ROOTS.AUTH_DEMO}/centered/sign-in`,
      signUp: `${ROOTS.AUTH_DEMO}/centered/sign-up`,
      resetPassword: `${ROOTS.AUTH_DEMO}/centered/reset-password`,
      updatePassword: `${ROOTS.AUTH_DEMO}/centered/update-password`,
      verify: `${ROOTS.AUTH_DEMO}/centered/verify`,
    },
  },
  // DASHBOARD
  dashboard: {
    root: ROOTS.DASHBOARD,
    mail: `${ROOTS.DASHBOARD}/mail`,
    chat: `${ROOTS.DASHBOARD}/chat`,
    permission: `${ROOTS.DASHBOARD}/permission`,
    workflow: {
      root: `${ROOTS.DASHBOARD}/workflows`,
      new: `${ROOTS.DASHBOARD}/workflows/new/workflow-information`,
      details: (id: string) => `${ROOTS.DASHBOARD}/workflows/${id}`,
      edit: (id: string) => `${ROOTS.DASHBOARD}/workflows/${id}/edit`,
    },
    knowledgebase: {
      root: `${ROOTS.DASHBOARD}knowledge-base/details`,
      search: `${ROOTS.DASHBOARD}knowledge-base/search`,
    },
    copilot: {
      root: `${ROOTS.DASHBOARD}copilot`,
    },
    agent: {
      root: `${ROOTS.DASHBOARD}agents`,
      new: `${ROOTS.DASHBOARD}agents/new`,
      flow: `${ROOTS.DASHBOARD}agents/flow`,
      chat: (agentKey: string) => `${ROOTS.DASHBOARD}agents/${agentKey}`,
      edit: (agentKey: string) => `${ROOTS.DASHBOARD}agents/${agentKey}/edit`,
      flowEdit: (agentKey: string) => `${ROOTS.DASHBOARD}agents/${agentKey}/flow`,
      conversation: (agentKey: string, conversationKey: string) => `${ROOTS.DASHBOARD}agent/${agentKey}/conv/${conversationKey}`,
    },
  },
};
