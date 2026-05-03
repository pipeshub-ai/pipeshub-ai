# PipesHub Code Samples

This document contains code samples referenced in the [project-plan.md](./project-plan.md).

---

## 1. Stateless Component Example (Button)

```tsx
// components/ui/button.tsx
import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '@/lib/utils';
import { Spinner } from './spinner';

// TypeScript union types for variants
type ButtonVariant = 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'link';
type ButtonSize = 'default' | 'sm' | 'lg' | 'icon';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  asChild?: boolean;
  loading?: boolean;
}

// Base styles applied to all buttons
const baseStyles = 'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 disabled:pointer-events-none disabled:opacity-50';

// Variant styles as Record objects
const variantStyles: Record<ButtonVariant, string> = {
  default: 'bg-primary text-primary-foreground hover:bg-primary/90',
  destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
  outline: 'border border-input bg-background hover:bg-accent',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
  ghost: 'hover:bg-accent hover:text-accent-foreground',
  link: 'text-primary underline-offset-4 hover:underline',
};

const sizeStyles: Record<ButtonSize, string> = {
  default: 'h-10 px-4 py-2',
  sm: 'h-9 rounded-md px-3',
  lg: 'h-11 rounded-md px-8',
  icon: 'h-10 w-10',
};

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', asChild = false, loading, children, disabled, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        className={cn(baseStyles, variantStyles[variant], sizeStyles[size], className)}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? <Spinner className="mr-2 h-4 w-4" /> : null}
        {children}
      </Comp>
    );
  }
);
Button.displayName = 'Button';

export { Button };
export type { ButtonVariant, ButtonSize };
```

---

## 2. Stateful Component Example (KbDashboard)

```tsx
// app/(dashboard)/knowledge-base/components/kb-dashboard.tsx
'use client';

import { useKnowledgeBases } from '../hooks/use-knowledge-base';
import { useKbStore } from '../store';
import { KbCard } from './kb-card';
import { CreateKbDialog } from './create-kb-dialog';
import { EmptyState, LoadingState, ErrorState } from '@/components/data-display';
import { Button } from '@/components/ui';
import { useBoolean } from '@/lib/hooks';

export function KbDashboard() {
  const { knowledgeBases, isLoading, error, refetch } = useKnowledgeBases();
  const createDialog = useBoolean(false);
  const setSelectedKb = useKbStore((state) => state.setSelectedKb);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={error.message} onRetry={refetch} />;
  if (!knowledgeBases.length) {
    return (
      <EmptyState
        title="No Knowledge Bases"
        description="Create your first knowledge base to get started"
        action={<Button onClick={createDialog.onTrue}>Create Knowledge Base</Button>}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Knowledge Bases</h1>
        <Button onClick={createDialog.onTrue}>Create New</Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {knowledgeBases.map((kb) => (
          <KbCard
            key={kb.id}
            kb={kb}
            onClick={() => setSelectedKb(kb)}
          />
        ))}
      </div>

      <CreateKbDialog
        open={createDialog.value}
        onClose={createDialog.onFalse}
        onSuccess={refetch}
      />
    </div>
  );
}
```

---

## 3. Query Params Pattern (Page Component)

```tsx
// app/(dashboard)/knowledge-base/page.tsx
'use client';

import { useSearchParams } from 'next/navigation';
import { KbDashboard } from './components/kb-dashboard';
import { KbDetailView } from './components/kb-detail-view';
import { FolderView } from './components/folder-view';

export default function KnowledgeBasePage() {
  const searchParams = useSearchParams();
  const kbId = searchParams.get('kbId');
  const folderId = searchParams.get('folderId');

  // Render different views based on query params
  if (kbId && folderId) {
    return <FolderView kbId={kbId} folderId={folderId} />;
  }
  if (kbId) {
    return <KbDetailView kbId={kbId} />;
  }
  return <KbDashboard />;
}
```

---

## 4. Zustand Store Factory

```tsx
// lib/store/create-store.ts
import { create, StateCreator } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

export function createStore<T>(
  name: string,
  initializer: StateCreator<T, [['zustand/immer', never]], []>,
  options?: { persist?: boolean }
) {
  const store = create<T>()(
    devtools(
      immer(initializer),
      { name }
    )
  );

  if (options?.persist) {
    return create<T>()(
      devtools(
        persist(
          immer(initializer),
          { name }
        ),
        { name }
      )
    );
  }

  return store;
}
```

---

## 5. Auth Store (Page-Level Store with Persistence)

```tsx
// app/(auth)/sign-in/store.ts
import { create } from 'zustand';
import { persist, devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { User } from './types';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

interface AuthActions {
  setUser: (user: User | null) => void;
  setTokens: (access: string, refresh: string) => void;
  logout: () => void;
  setLoading: (loading: boolean) => void;
}

type AuthStore = AuthState & AuthActions;

const initialState: AuthState = {
  user: null,
  accessToken: null,
  refreshToken: null,
  isAuthenticated: false,
  isLoading: true,
};

export const useAuthStore = create<AuthStore>()(
  devtools(
    persist(
      immer((set) => ({
        ...initialState,

        setUser: (user) =>
          set((state) => {
            state.user = user;
            state.isAuthenticated = !!user;
            state.isLoading = false;
          }),

        setTokens: (access, refresh) =>
          set((state) => {
            state.accessToken = access;
            state.refreshToken = refresh;
          }),

        logout: () =>
          set((state) => {
            state.user = null;
            state.accessToken = null;
            state.refreshToken = null;
            state.isAuthenticated = false;
          }),

        setLoading: (loading) =>
          set((state) => {
            state.isLoading = loading;
          }),
      })),
      {
        name: 'auth-storage',
        partialize: (state) => ({
          accessToken: state.accessToken,
          refreshToken: state.refreshToken,
        }),
      }
    ),
    { name: 'AuthStore' }
  )
);

// Selectors
export const selectUser = (state: AuthStore) => state.user;
export const selectIsAuthenticated = (state: AuthStore) => state.isAuthenticated;
export const selectIsLoading = (state: AuthStore) => state.isLoading;
```

---

## 6. Global App Store

```tsx
// lib/store/app-store.ts
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

interface AppState {
  theme: 'light' | 'dark' | 'system';
  sidebarOpen: boolean;
  locale: 'en' | 'de';
}

interface AppActions {
  setTheme: (theme: AppState['theme']) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setLocale: (locale: AppState['locale']) => void;
}

type AppStore = AppState & AppActions;

export const useAppStore = create<AppStore>()(
  devtools(
    immer((set) => ({
      theme: 'system',
      sidebarOpen: true,
      locale: 'en',

      setTheme: (theme) =>
        set((state) => {
          state.theme = theme;
        }),

      toggleSidebar: () =>
        set((state) => {
          state.sidebarOpen = !state.sidebarOpen;
        }),

      setSidebarOpen: (open) =>
        set((state) => {
          state.sidebarOpen = open;
        }),

      setLocale: (locale) =>
        set((state) => {
          state.locale = locale;
        }),
    })),
    { name: 'AppStore' }
  )
);
```

---

## 7. Axios Instance with Interceptors

```tsx
// lib/api/axios-instance.ts
import axios, { AxiosError, AxiosRequestConfig } from 'axios';
import { useAuthStore } from '@/features/auth/store/auth-store';
import { ProcessedError, ErrorType } from './api-error';
import { toast } from 'sonner';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL;

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token
apiClient.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().accessToken;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor - handle errors globally
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const processedError = processError(error);

    // Global error handling for auth/network errors
    if (processedError.type === ErrorType.AUTHENTICATION_ERROR) {
      useAuthStore.getState().logout();
      toast.error('Session expired. Please sign in again.');
      window.location.href = '/sign-in';
    } else if (processedError.type === ErrorType.NETWORK_ERROR) {
      toast.error('Network error. Please check your connection.');
    }
    // Business logic errors NOT shown here - handled by components

    return Promise.reject(processedError);
  }
);

function processError(error: AxiosError): ProcessedError {
  // Port the error processing logic from current codebase
  // ... (same as current axios.tsx)
}

export { apiClient };
```

---

## 8. Page-Level API Service

```tsx
// app/(dashboard)/knowledge-base/api.ts
import { apiClient } from '@/lib/api';
import type { KnowledgeBase, Folder, Record, CreateKbPayload } from './types';

const BASE_URL = '/api/v1/knowledgeBase';

export const KnowledgeBaseApi = {
  // List all knowledge bases
  async list(): Promise<KnowledgeBase[]> {
    const { data } = await apiClient.get<{ knowledgeBases: KnowledgeBase[] }>(BASE_URL);
    return data.knowledgeBases;
  },

  // Get single knowledge base
  async get(id: string): Promise<KnowledgeBase> {
    const { data } = await apiClient.get<KnowledgeBase>(`${BASE_URL}/${id}`);
    return data;
  },

  // Create knowledge base
  async create(payload: CreateKbPayload): Promise<KnowledgeBase> {
    const { data } = await apiClient.post<KnowledgeBase>(BASE_URL, payload);
    return data;
  },

  // Update knowledge base
  async update(id: string, payload: Partial<CreateKbPayload>): Promise<KnowledgeBase> {
    const { data } = await apiClient.patch<KnowledgeBase>(`${BASE_URL}/${id}`, payload);
    return data;
  },

  // Delete knowledge base
  async delete(id: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`);
  },

  // Folder operations
  async getFolderContents(
    kbId: string,
    folderId?: string,
    params?: { page: number; limit: number; search?: string }
  ) {
    const url = folderId
      ? `${BASE_URL}/${kbId}/folders/${folderId}/contents`
      : `${BASE_URL}/${kbId}/contents`;
    const { data } = await apiClient.get(url, { params });
    return data;
  },

  async createFolder(kbId: string, parentId: string | null, name: string): Promise<Folder> {
    const { data } = await apiClient.post<Folder>(`${BASE_URL}/${kbId}/folders`, {
      name,
      parentId,
    });
    return data;
  },

  // ... more methods
};
```

---

## 9. Custom Hook with Zustand Integration

```tsx
// app/(dashboard)/knowledge-base/hooks/use-knowledge-base.ts
import { useCallback, useEffect } from 'react';
import { KnowledgeBaseApi } from '../api';
import { useKbStore } from '../store';
import { toast } from 'sonner';
import type { ProcessedError } from '@/lib/api';

export function useKnowledgeBases() {
  const {
    knowledgeBases,
    isLoading,
    error,
    setKnowledgeBases,
    setLoading,
    setError,
  } = useKbStore();

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await KnowledgeBaseApi.list();
      setKnowledgeBases(data);
    } catch (err) {
      const error = err as ProcessedError;
      setError(error);
      // Business logic errors shown at component level
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, [setKnowledgeBases, setLoading, setError]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return {
    knowledgeBases,
    isLoading,
    error,
    refetch: fetch,
  };
}
```

---

## 10. SSE Streaming Manager

```tsx
// app/(dashboard)/chat/streaming-manager.ts
export interface StreamingState {
  messageId: string | null;
  content: string;
  citations: Citation[];
  isActive: boolean;
  controller: AbortController | null;
  statusMessage: string;
}

class StreamingManager {
  private static instance: StreamingManager;
  private conversationStates: Map<string, StreamingState> = new Map();
  private listeners: Set<() => void> = new Set();

  static getInstance(): StreamingManager {
    if (!StreamingManager.instance) {
      StreamingManager.instance = new StreamingManager();
    }
    return StreamingManager.instance;
  }

  subscribe(callback: () => void) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  private notify() {
    this.listeners.forEach((cb) => cb());
  }

  getState(conversationId: string): StreamingState | undefined {
    return this.conversationStates.get(conversationId);
  }

  updateState(conversationId: string, updates: Partial<StreamingState>) {
    const current = this.conversationStates.get(conversationId) || this.createInitialState();
    this.conversationStates.set(conversationId, { ...current, ...updates });
    this.notify();
  }

  async startStream(conversationId: string, message: string) {
    const controller = new AbortController();
    this.updateState(conversationId, {
      isActive: true,
      controller,
      content: '',
      citations: [],
    });

    try {
      const response = await fetch(`${API_URL}/chat/${conversationId}/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          Authorization: `Bearer ${useAuthStore.getState().accessToken}`,
        },
        body: JSON.stringify({ query: message }),
        signal: controller.signal,
      });

      await this.processStream(conversationId, response);
    } catch (error) {
      if (error.name !== 'AbortError') {
        this.updateState(conversationId, {
          isActive: false,
          statusMessage: 'Stream failed',
        });
      }
    }
  }

  private async processStream(conversationId: string, response: Response) {
    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6));
          this.handleStreamEvent(conversationId, data);
        }
      }
    }

    this.updateState(conversationId, { isActive: false });
  }

  private handleStreamEvent(conversationId: string, data: any) {
    const current = this.getState(conversationId);
    if (!current) return;

    switch (data.event) {
      case 'content':
        this.updateState(conversationId, {
          content: current.content + data.content,
        });
        break;
      case 'citation':
        this.updateState(conversationId, {
          citations: [...current.citations, data.citation],
        });
        break;
      case 'complete':
        this.updateState(conversationId, {
          messageId: data.messageId,
          isActive: false,
        });
        break;
    }
  }

  cancelStream(conversationId: string) {
    const state = this.getState(conversationId);
    state?.controller?.abort();
    this.updateState(conversationId, { isActive: false });
  }

  private createInitialState(): StreamingState {
    return {
      messageId: null,
      content: '',
      citations: [],
      isActive: false,
      controller: null,
      statusMessage: '',
    };
  }
}

export const streamingManager = StreamingManager.getInstance();
```

---

## 11. WebSocket Manager for Notifications

```tsx
// app/(dashboard)/notifications/websocket-manager.ts
import { useAuthStore } from '@/app/(auth)/sign-in/store';
import { useNotificationStore } from './store';
import { toast } from 'sonner';

class WebSocketManager {
  private static instance: WebSocketManager;
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;

  static getInstance(): WebSocketManager {
    if (!WebSocketManager.instance) {
      WebSocketManager.instance = new WebSocketManager();
    }
    return WebSocketManager.instance;
  }

  connect() {
    const token = useAuthStore.getState().accessToken;
    if (!token || this.ws?.readyState === WebSocket.OPEN) return;

    const wsUrl = `${process.env.NEXT_PUBLIC_WS_URL}?token=${token}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.handleMessage(data);
    };

    this.ws.onclose = () => {
      this.attemptReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  private handleMessage(data: { type: string; payload: any }) {
    switch (data.type) {
      case 'UPLOAD_COMPLETE':
        toast.success(`File "${data.payload.fileName}" uploaded successfully`);
        break;
      case 'INDEXING_COMPLETE':
        toast.success(`Document indexed successfully`);
        break;
      case 'ERROR':
        toast.error(data.payload.message);
        break;
      default:
        console.log('Unknown message type:', data.type);
    }
  }

  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    setTimeout(() => {
      console.log(`Reconnecting... attempt ${this.reconnectAttempts}`);
      this.connect();
    }, delay);
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

export const wsManager = WebSocketManager.getInstance();
```

---

## 12. i18n Configuration

```tsx
// lib/i18n/config.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// Import locale files
import enCommon from './locales/en/common.json';
import enAuth from './locales/en/auth.json';
import deCommon from './locales/de/common.json';
import deAuth from './locales/de/auth.json';
// ... more imports

const resources = {
  en: {
    common: enCommon,
    auth: enAuth,
    // ...
  },
  de: {
    common: deCommon,
    auth: deAuth,
    // ...
  },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs: ['en', 'de'],
    defaultNS: 'common',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
  });

export default i18n;
```

---

## 13. i18n Provider

```tsx
// lib/i18n/provider.tsx
'use client';

import { I18nextProvider } from 'react-i18next';
import i18n from './config';

export function I18nProvider({ children }: { children: React.ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
```

---

## 14. Theme Provider

```tsx
// components/theme/theme-provider.tsx
'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import { useAppStore } from '@/lib/store/app-store';

type Theme = 'dark' | 'light' | 'system';

interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  resolvedTheme: 'dark' | 'light';
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { theme, setTheme } = useAppStore();
  const [resolvedTheme, setResolvedTheme] = useState<'dark' | 'light'>('light');

  useEffect(() => {
    const root = window.document.documentElement;

    const applyTheme = (newTheme: 'dark' | 'light') => {
      root.classList.remove('light', 'dark');
      root.classList.add(newTheme);
      setResolvedTheme(newTheme);
    };

    if (theme === 'system') {
      const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
      applyTheme(systemTheme);

      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const handleChange = (e: MediaQueryListEvent) => {
        applyTheme(e.matches ? 'dark' : 'light');
      };
      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    } else {
      applyTheme(theme);
    }
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolvedTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
};
```

---

## 15. Root Layout

```tsx
// app/layout.tsx
import { Inter } from 'next/font/google';
import { ThemeProvider } from '@/components/theme';
import { I18nProvider } from '@/lib/i18n';
import { Toaster } from 'sonner';
import '@/styles/globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata = {
  title: 'PipesHub',
  description: 'AI-powered knowledge management',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>
        <I18nProvider>
          <ThemeProvider>
            {children}
            <Toaster position="top-right" richColors />
          </ThemeProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
```
