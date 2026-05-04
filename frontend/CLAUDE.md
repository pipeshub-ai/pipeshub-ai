# CLAUDE.md - PipesHub Dashboard UI

## Project Overview

PipesHub is an AI-powered knowledge management dashboard on **Next.js** (App Router, client-rendered React).

**Tech Stack:**
- Next.js 14+ (App Router, CSR-only with `'use client'`)
- TypeScript (strict mode)
- Zustand (state management)
- Radix UI Themes v3.2.1 (styling & components) - **No Tailwind CSS**
- Google Fonts (Manrope) + Google Material Icons
- React Hook Form + Zod (complex forms) / Native (simple forms)
- SSE (chat streaming) + WebSocket (notifications)
- i18n: German, English, Spanish, Hindi

## Naming: Collections vs Knowledge Base

**Important:** The feature formerly called "Knowledge Base" is now called **"Collections"** in the UI.

| Context | Name Used |
|---------|-----------|
| UI labels & text | "Collections" or "All Records" |
| Route path | `/knowledge-base` |
| API endpoints | `/api/v1/knowledgeBase` |
| Code (types, stores, variables) | `KnowledgeBase`, `kb`, `kbId` |
| Component files | `sidebar.tsx`, `header.tsx`, `filter-bar.tsx`, `kb-data-table.tsx` |

**Collections mode sidebar sections:**
- WORKSPACE (user's own collections)
- SHARED (collections shared by others)
- PRIVATE (private collections)

**All Records mode sidebar sections:**
- All Records (shows all records across sources)
- Collections (flat list of collections - clicking filters records)
- Connectors (Slack, Google Drive, Jira, etc.)

## Folder Structure

```
src/
├── app/                    # Next.js App Router
│   ├── (auth)/             # Auth route group
│   │   ├── sign-in/        # Each page has: page.tsx, api.ts, store.ts, types.ts, components/
│   │   ├── sign-up/
│   │   └── reset-password/
│   ├── (dashboard)/        # Main app (uses query params, not dynamic routes)
│   │   ├── knowledge-base/ # Collections page - Uses ?kbId=xxx&folderId=xxx
│   │   │   ├── page.tsx
│   │   │   ├── api.ts      # Page-specific API calls
│   │   │   ├── store.ts    # Page-specific Zustand store
│   │   │   ├── types.ts    # Page-specific types
│   │   │   └── components/ # Page-specific components
│   │   ├── agents/         # Uses ?agentId=xxx
│   │   ├── chat/           # Uses ?conversationId=xxx
│   │   ├── connectors/
│   │   ├── account/
│   │   ├── users/
│   │   ├── groups/
│   │   └── notifications/
│   ├── layout.tsx          # Root layout (providers)
│   └── page.tsx            # Redirect to dashboard or auth
│
├── components/             # Shared UI components (stateless)
│   ├── ui/                 # Radix Themes components (MaterialIcon, Select, etc.)
│   ├── form/               # React Hook Form wrappers
│   ├── layout/             # Sidebar, header, breadcrumbs
│   ├── data-display/       # DataTable, empty/error/loading states
│   ├── feedback/           # Toast, confirmation dialogs
│   └── icons/              # Google Material Icons wrapper
│
├── lib/                    # Core utilities
│   ├── api/                # API layer
│   │   ├── axios-instance.ts  # Axios client with interceptors
│   │   ├── api-error.ts       # ErrorType & ProcessedError
│   │   ├── swr-fetcher.ts     # SWR fetcher
│   │   ├── streaming.ts       # SSE streaming (native fetch)
│   │   └── index.ts           # Barrel export
│   ├── store/              # Global Zustand stores
│   │   └── auth-store.ts      # Auth state (tokens, user)
│   ├── hooks/              # Global utility hooks
│   ├── utils/              # formatters, validators
│   ├── constants/          # Routes, storage keys, API endpoints
│   └── i18n/               # i18next config and locales
│
├── styles/                 # Global CSS and fonts
├── types/                  # Shared TypeScript types
├── config/                 # Site config and env variables
└── middleware.ts           # Auth redirect middleware
```

## Naming Conventions

### Files & Folders
| Type | Convention | Example |
|------|------------|---------|
| Folders | kebab-case | `knowledge-base/`, `data-display/` |
| Components | kebab-case.tsx | `kb-card.tsx`, `message-bubble.tsx` |
| Hooks | use-*.ts | `use-auth.ts`, `use-debounce.ts` |
| Stores | store.ts | Page-level store (`app/(dashboard)/chat/store.ts`) |
| APIs | api.ts | Page-level API (`app/(dashboard)/chat/api.ts`) |
| Types | types.ts | Page-specific types |
| Utils | kebab-case.ts | `format-date.ts`, `cn.ts` |

### Code Style
| Type | Convention | Example |
|------|------------|---------|
| Components | PascalCase | `KbCard`, `MessageBubble` |
| Hooks | camelCase with `use` | `useAuth`, `useDebounce` |
| Functions | camelCase | `formatDate`, `handleSubmit` |
| Constants | SCREAMING_SNAKE | `STORAGE_KEYS.JWT_TOKEN` |
| Types/Interfaces | PascalCase | `KnowledgeBase`, `User` |

## Key Architecture Patterns

### Component Types
- **Stateless** (`/components/ui/`): Pure presentational, no internal state, uses TypeScript union types for variants
- **Stateful** (`/app/(dashboard)/*/components/`): Page-specific with hooks, state, business logic

### Page-Level Co-location
Each page folder contains its own resources:
```
app/(dashboard)/[page]/
├── page.tsx        # Main page component (reads query params)
├── api.ts          # Page-specific API calls
├── store.ts        # Page-specific Zustand store
├── types.ts        # Page-specific TypeScript types
└── components/     # Page-specific React components
    ├── some-component.tsx
    └── index.ts    # Barrel export
```

### Query Params (Not Dynamic Routes)
Use query params instead of dynamic route segments:
- `/knowledge-base?nodeType=kb&nodeId=123` (not `/knowledge-base/[kbId]/[folderId]`)
- `/chat?conversationId=789` (not `/chat/[conversationId]`)
- `/agents?agentId=001` (not `/agents/[agentId]`)

#### Knowledge Base Page Modes

The knowledge-base page supports two modes via query params:

| Mode | URL Pattern | Description |
|------|-------------|-------------|
| Collections (default) | `/knowledge-base` | Folder tree navigation, CRUD operations |
| Collections with node | `/knowledge-base?nodeType=kb&nodeId=xxx` | View specific collection/folder |
| All Records | `/knowledge-base?view=all-records` | Flat view of all records across sources |

**Key differences between modes:**

| Feature | Collections Mode | All Records Mode |
|---------|------------------|------------------|
| Sidebar | Folder tree (WORKSPACE/SHARED/PRIVATE) | Flat collections + Connectors |
| Header buttons | Find, Refresh, New, Share, View toggle | Find, Refresh only |
| Collection click | Navigate into collection | Filter records (stay on page) |
| Data source | Single KB/folder content | All records across KBs + connectors |
| Source column | Hidden | Shown (displays source name + icon) |

### Styling - Always with Radix UI Theme

**Important:** Always refer to `app/globals.css` for available CSS variables (colors, spacing, radius) before styling.
**Important:** Always refer to `docs/style-guide.md` for Radix UI usage guidelines.

**Layout Components:**
```tsx
import { Flex, Box, Grid, Text, Heading } from '@radix-ui/themes';

// Instead of: <div className="flex items-center gap-2">
<Flex align="center" gap="2">

// Instead of: <p className="text-sm text-gray-600">
<Text size="2" color="gray">
```

**Inline Styles for Custom Values:**
```tsx
// For values outside Radix's scale (fixed widths, gradients, etc.)
<Box style={{ width: '300px', backgroundColor: 'var(--olive-1)' }}>
```

**Interactive States with React State:**
```tsx
const [isHovered, setIsHovered] = useState(false);

<button
  onMouseEnter={() => setIsHovered(true)}
  onMouseLeave={() => setIsHovered(false)}
  style={{ backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent' }}
>
```

**Theme Configuration (in layout.tsx):**
```tsx
<Theme accentColor="jade" grayColor="olive" appearance="light" radius="medium">
```

### State Management (Zustand)
- Uses immer middleware for immutable updates
- Uses devtools middleware for debugging
- Auth store persists tokens to localStorage
- Stores are page-scoped, co-located with their page

### API Layer

**Structure (`lib/api/`):**
```
lib/api/
├── axios-instance.ts   # Configured axios with interceptors
├── api-error.ts        # ErrorType enum & ProcessedError interface
├── swr-fetcher.ts      # SWR fetcher using axios
├── streaming.ts        # SSE streaming (native fetch - axios limitation)
└── index.ts            # Barrel export
```

**Axios Instance (`lib/api/axios-instance.ts`):**
- Centralized axios client with `withCredentials: true`
- **Request interceptor**: Automatically adds `Authorization: Bearer <token>` from auth store
- **Response interceptor**:
  - Handles 401 with automatic token refresh
  - Queues failed requests during refresh, retries after success
  - Processes errors into standardized `ProcessedError` format
  - Redirects to `/login` on auth failure

**Auth Store (`lib/store/auth-store.ts`):**
- Global Zustand store for authentication
- Persists `accessToken` and `refreshToken` to localStorage
- Used by axios interceptors for token management

**Usage in page-level API files:**
```tsx
// app/(main)/knowledge-base/api.ts (Collections page)
import { apiClient } from '@/lib/api';

// Note: API still uses "knowledgeBase" naming, UI shows "Collections"
export const KnowledgeBaseApi = {
  async listKnowledgeBases() {
    const { data } = await apiClient.get<{ knowledgeBases: any[] }>(BASE_URL);
    return data;
  },
};
```

**Public API (no auth):**
- `app/(public)/api.ts` uses a separate `publicClient` without interceptors
- Used for unauthenticated endpoints (OTP login flow)

**SSE Streaming:**
- Uses native `fetch` in `lib/api/streaming.ts` (axios doesn't support streaming)
- Gets token from auth store for Authorization header

### Real-Time Communication
- **SSE (Server-Sent Events)**: Chat message streaming (`app/(dashboard)/chat/streaming-manager.ts`)
- **WebSocket**: Notifications (`app/(dashboard)/notifications/websocket-manager.ts`)

## Common Commands

```bash
# Development
npm run dev

# Build
npm run build

# Lint
npm run lint

# Type check
npm run type-check
```

## Key Files to Know

- `app/(main)/layout.tsx` - Root layout with Radix Theme provider and SWRConfig
- `app/globals.css` - CSS variables (colors, spacing, radius) and custom utilities
- `lib/api/axios-instance.ts` - Centralized axios with request/response interceptors
- `lib/api/api-error.ts` - ErrorType enum and ProcessedError interface
- `lib/api/streaming.ts` - SSE streaming helper using native fetch
- `lib/store/auth-store.ts` - Global auth store (tokens, user, login/logout)
- `app/(main)/chat/streaming-manager.ts` - SSE chat streaming
- `app/(main)/notifications/websocket-manager.ts` - WebSocket notifications

## Knowledge Base Page Architecture

The knowledge-base page (`app/(main)/knowledge-base/`) consolidates both "Collections" and "All Records" views into a single page with mode-aware components.

### File Structure
```
app/(main)/knowledge-base/
├── page.tsx              # Main page with view mode detection
├── api.ts                # API calls for collections and records
├── store.ts              # Zustand store (collections + all-records state)
├── types.ts              # Types for both modes (PageViewMode, AllRecordItem, etc.)
├── mock-data.ts          # Mock data for connectors and records
└── components/
    ├── index.ts          # Barrel export
    ├── sidebar.tsx       # Mode-aware sidebar (tree vs flat list)
    ├── header.tsx        # Mode-aware header (different action buttons)
    ├── filter-bar.tsx    # Mode-aware filters (Source filter in all-records)
    ├── kb-data-table.tsx # Data table with optional Source column
    ├── create-folder-dialog.tsx
    ├── upload-data-sidebar.tsx
    ├── move-folder-sidebar.tsx
    └── replace-file-dialog.tsx
```

### Mode-Aware Components

Components accept a `pageViewMode: PageViewMode` prop (`'collections' | 'all-records'`):

| Component | Collections Mode | All Records Mode |
|-----------|------------------|------------------|
| `Sidebar` | Folder tree with WORKSPACE/SHARED/PRIVATE | Flat collections list + Connectors |
| `Header` | Find, Refresh, New dropdown, Share, View toggle | Find, Refresh only |
| `FilterBar` | Type, Status, Size, Date filters | Same + Source filter |
| `KbDataTable` | Standard columns | Adds Source column (`showSourceColumn` prop) |

### Key Types

```typescript
// View mode for the page
type PageViewMode = 'collections' | 'all-records';

// Connector types for All Records mode
type ConnectorType = 'slack' | 'google-drive' | 'jira' | 'dropbox' | 'notion';

// Sidebar selection in All Records mode
type AllRecordsSidebarSelection =
  | { type: 'all' }
  | { type: 'collection'; id: string; name: string }
  | { type: 'connector'; connectorType: ConnectorType; itemId?: string };

// Extended record with source info (All Records mode)
interface AllRecordItem extends KnowledgeHubTableNode {
  sourceName: string;
  sourceType: 'collection' | ConnectorType;
  sourceIcon?: string;
}
```

### Store Structure

The store (`store.ts`) contains state for both modes:

```typescript
// Collections mode state
filter, sort, viewMode, selectedItems, breadcrumbs, tableData, ...

// All Records mode state
allRecords, connectors, flatCollections, allRecordsSidebarSelection,
selectedRecords, expandedSections, allRecordsFilter, allRecordsSort, ...
```

## Development Guidelines

1. **Always use `'use client'`** - This is a CSR-only app
2. **Prefer editing existing files** over creating new ones
3. **Follow the page-level co-location pattern** - api.ts, store.ts, types.ts, components/ per page
4. **Use query params** for navigation, not dynamic route segments
5. **Use Radix UI Themes components** - Flex, Box, Text, Grid, Card, etc.
6. **Use inline styles** for custom values (widths, gradients, specific colors)
7. **Use CSS variables** from globals.css: `var(--olive-1)`, `var(--space-4)`, `var(--radius-2)`
8. **Use Zustand stores** for page state, not prop drilling
9. **Use the API service pattern** - Don't call axios directly from components
10. **Error handling**: Global errors (auth, network) in interceptors; business errors in components
11. **i18n**: All user-facing strings should use translation keys

## Responsiveness

**Mobile:** Full mobile support with touch-friendly UI

**Browsers:** Chrome, Brave, Firefox, Safari

**Screen Sizes:**
- Mobile: 375px - 428px
- MacBook 13": 1280 x 800
- MacBook 15": 1440 x 900
- Desktop 24": 1920 x 1080+

**Guidelines:**
- Use Radix responsive props: `columns={{ initial: "1", md: "2" }}`
- Touch targets minimum 44px on mobile
- Collapsible sidebar on mobile, persistent on desktop

## CSS Variables Reference

Available in `globals.css`:

**Colors:**
- `--olive-1` to `--olive-12` (gray scale with green tint)
- `--accent-1` to `--accent-12` (emerald)
- `--neutral-1` to `--neutral-12`

**Spacing:** `--space-1` to `--space-9`

**Border Radius:** `--radius-1` to `--radius-6`, `--radius-full`

**Font:** Manrope (configured via `.radix-themes { --default-font-family }`)

## Allowed className Usage

Only two className patterns are valid (everything else uses Radix components + inline styles):

1. `className="material-icons-outlined"` - Required for Google Material Icons font
2. `className="no-scrollbar"` - Custom utility for hiding scrollbars
