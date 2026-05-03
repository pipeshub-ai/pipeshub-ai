# PipesHub Frontend Design Overhaul - Architecture Plan

## Project Overview

**Stack:**
- Next.js 14+ (App Router, CSR-only with `'use client'`)
- TypeScript (strict mode)
- Zustand (state management)
- Radix UI Themes v3.2.1 (styling & components) - **No Tailwind CSS**
- Google Fonts (Manrope) + Google Material Icons
- React Hook Form + Zod (complex forms) / Native (simple forms)
- SSE (chat streaming) + WebSocket (notifications)
- i18n: German, English

**Approach:** Migrated from the legacy React/Vite/MUI app to this stack, reusing hooks, APIs, and patterns where practical.

> **Note:** Code samples for all patterns described in this document are available in [code-samples.md](./code-samples.md).

---

## 1. Folder Structure

---

## 2. Naming Conventions

### Files & Folders
| Type | Convention | Example |
|------|------------|---------|
| Folders | kebab-case | `knowledge-base/`, `data-display/` |
| Components | kebab-case.tsx | `kb-card.tsx`, `message-bubble.tsx` |
| Hooks | use-*.ts | `use-auth.ts`, `use-debounce.ts` |
| Stores | *-store.ts | `auth-store.ts`, `chat-store.ts` |
| Services/APIs | *-api.ts | `kb-api.ts`, `auth-api.ts` |
| Types | types.ts or *.types.ts | `types.ts` |
| Utils | kebab-case.ts | `format-date.ts`, `cn.ts` |
| Constants | kebab-case.ts | `storage-keys.ts`, `routes.ts` |

### Code Style
| Type | Convention | Example |
|------|------------|---------|
| Components | PascalCase | `KbCard`, `MessageBubble` |
| Hooks | camelCase with `use` | `useAuth`, `useDebounce` |
| Functions | camelCase | `formatDate`, `handleSubmit` |
| Constants | SCREAMING_SNAKE | `STORAGE_KEYS.JWT_TOKEN` |
| Types/Interfaces | PascalCase | `KnowledgeBase`, `User` |
| Enums | PascalCase | `ErrorType.SERVER_ERROR` |

---

## 3. Component Architecture & Styling

### Styling - Always with Radix UI Theme

**Important:** Always refer to `app/globals.css` for available CSS variables (colors, spacing, radius) before styling.

Use Radix Themes layout components instead of HTML elements with utility classes:

```tsx
import { Flex, Box, Grid, Text, Heading, Card } from '@radix-ui/themes';

// Layout
<Flex align="center" gap="2">         // Instead of: <div className="flex items-center gap-2">
<Box p="4" m="2">                      // Instead of: <div className="p-4 m-2">
<Grid columns="2" gap="4">             // Instead of: <div className="grid grid-cols-2 gap-4">

// Typography
<Text size="2" color="gray">          // Instead of: <p className="text-sm text-gray-600">
<Heading size="7" weight="medium">    // Instead of: <h1 className="text-2xl font-semibold">
```

### Inline Styles for Custom Values
For values outside Radix's scale (fixed widths, gradients, specific colors):

```tsx
<Box style={{ width: '300px', backgroundColor: 'var(--olive-1)' }}>
<Flex style={{ background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))' }}>
```

### Interactive States with React State
Use React state for hover/focus effects instead of CSS pseudo-classes:

```tsx
const [isHovered, setIsHovered] = useState(false);

<button
  onMouseEnter={() => setIsHovered(true)}
  onMouseLeave={() => setIsHovered(false)}
  style={{ backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent' }}
>
```

### Theme Configuration
In `layout.tsx`:
```tsx
<Theme accentColor="jade" grayColor="olive" appearance="light" radius="medium">
```

### Stateless Components (`/components/ui/`)
Pure presentational components, typically wrapping Radix Themes components.

### Stateful Components (Page-level `/app/(dashboard)/*/components/`)
Page-specific components with hooks, state, and business logic. Co-located with their page.

### Query Params Pattern for Navigation
Pages use query params instead of dynamic routes for navigation.

---

## 4. Zustand Store Structure

### Store Factory Pattern
> See [code-samples.md#4-zustand-store-factory](./code-samples.md#4-zustand-store-factory) for implementation.

### Page-Level Store Example
> See [code-samples.md#5-auth-store-page-level-store-with-persistence](./code-samples.md#5-auth-store-page-level-store-with-persistence) for implementation.

### Global App Store
> See [code-samples.md#6-global-app-store](./code-samples.md#6-global-app-store) for implementation.

---

## 5. API Layer

### Axios Instance with Interceptors
> See [code-samples.md#7-axios-instance-with-interceptors](./code-samples.md#7-axios-instance-with-interceptors) for implementation.

### Page-Level API Service Pattern
> See [code-samples.md#8-page-level-api-service](./code-samples.md#8-page-level-api-service) for implementation.

### Custom Hook with Zustand Integration
> See [code-samples.md#9-custom-hook-with-zustand-integration](./code-samples.md#9-custom-hook-with-zustand-integration) for implementation.

---

## 6. Real-Time Communication

### SSE Streaming Manager (Ported)
> See [code-samples.md#10-sse-streaming-manager](./code-samples.md#10-sse-streaming-manager) for implementation.

### WebSocket for Notifications
> See [code-samples.md#11-websocket-manager-for-notifications](./code-samples.md#11-websocket-manager-for-notifications) for implementation.

---

## 7. i18n Setup

> See [code-samples.md#12-i18n-configuration](./code-samples.md#12-i18n-configuration) and [code-samples.md#13-i18n-provider](./code-samples.md#13-i18n-provider) for implementation.

---

## 8. Theme Setup

Theme is configured via Radix UI Themes provider in `layout.tsx`:

```tsx
import { Theme } from '@radix-ui/themes';

<Theme
  accentColor="jade"      // Green color palette
  grayColor="olive"       // Gray with green tint
  appearance="light"      // Light mode only (for now)
  radius="medium"         // Border radius scale
>
  {children}
</Theme>
```

Custom font override in `globals.css`:
```css
.radix-themes {
  --default-font-family: 'Manrope', sans-serif;
}
```

---

## 9. Root Layout (Provider Setup)

> See [code-samples.md#15-root-layout](./code-samples.md#15-root-layout) for implementation.

---

## 10. Migration Checklist

### Phase 1: Setup
- [x] Initialize Next.js 14 with App Router
- [x] Configure TypeScript (strict mode)
- [x] Install and configure Radix UI Themes v3.2.1
- [x] Configure Theme provider with jade/olive colors
- [x] Set up CSS variables in globals.css
- [ ] Set up Zustand with devtools
- [ ] Configure i18next for en/de

### Phase 2: Core Infrastructure
- [x] Port axios instance + interceptors (`lib/api/axios-instance.ts`)
- [x] Port error handling (`lib/api/api-error.ts` - ErrorType enum & ProcessedError)
- [x] Create SWR fetcher with axios (`lib/api/fetcher.ts`)
- [x] Create SSE streaming helper (`lib/api/streaming.ts` - native fetch)
- [x] Create global auth store (`lib/store/auth-store.ts` - Zustand with localStorage)
- [x] Update page-level APIs to use axios (`knowledge-base/api.ts`, `chat/api.ts`)
- [x] Create public API client (`app/(public)/api.ts` - no auth interceptors)
- [ ] Port utility hooks (`lib/hooks/`)
- [x] Create MaterialIcon component (`components/ui/MaterialIcon.tsx`)
- [x] Create Select wrapper (`components/ui/Select.tsx`)

### Phase 3: Pages (in order, each with co-located api.ts, store.ts, types.ts, components/)
- [ ] Auth pages (`app/(auth)/sign-in/`, `sign-up/`, `reset-password/`)
- [ ] Dashboard layout (`app/(dashboard)/layout.tsx`)
- [ ] Knowledge Base page (`app/(dashboard)/knowledge-base/`)
- [ ] Agents page (`app/(dashboard)/agents/`)
- [ ] Chat page with streaming (`app/(dashboard)/chat/`)
- [ ] Connectors page (`app/(dashboard)/connectors/`)
- [ ] Users page (`app/(dashboard)/users/`)
- [ ] Groups page (`app/(dashboard)/groups/`)
- [ ] Account page (`app/(dashboard)/account/`)

### Phase 4: Polish
- [ ] WebSocket notifications (`app/(dashboard)/notifications/`)
- [ ] Responsive design testing (see Responsiveness section)
- [ ] Dark mode support (currently light mode only)
- [ ] i18n completion (all translations)
- [ ] Error boundaries
- [ ] Loading states optimization

---

## 11. Responsiveness

### Mobile
- Full mobile support required
- Touch-friendly UI elements
- Responsive navigation (collapsible sidebar, mobile menu)

### Browser Support
- Chrome
- Brave
- Firefox
- Safari

### Screen Sizes
| Device | Size |
|--------|------|
| MacBook 13" | 1280 x 800 |
| MacBook 15" | 1440 x 900 |
| Desktop Monitor 24" | 1920 x 1080+ |
| Mobile | 375px - 428px width |

### Radix Responsive Props
Use responsive object syntax for breakpoint-specific values:

```tsx
<Grid columns={{ initial: "1", md: "2", lg: "3" }} gap="4">
<Text size={{ initial: "2", md: "3" }}>
```

### Guidelines
1. **Mobile-first approach** - Use `initial` for mobile, add `md`/`lg` for larger screens
2. **Test on real devices** - Don't rely solely on browser DevTools
3. **Touch targets** - Minimum 44px for interactive elements on mobile
4. **Sidebar behavior** - Collapsible on mobile, persistent on desktop
5. **Tables/DataGrids** - Horizontal scroll or card view on mobile

---

## 12. CSS Variables Reference

Available in `globals.css`:

### Colors
- `--olive-1` to `--olive-12` (gray scale with green tint)
- `--accent-1` to `--accent-12` (jade green)
- `--neutral-1` to `--neutral-12`

### Spacing
`--space-1` to `--space-9`

### Border Radius
`--radius-1` to `--radius-6`, `--radius-full`

### Allowed className Usage
Only two className patterns are valid:
1. `className="material-icons-outlined"` - Required for Google Material Icons font
2. `className="no-scrollbar"` - Custom utility for hiding scrollbars
