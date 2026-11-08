# Frontend — agent guide

UI conventions for `frontend/`. For backend or repo-wide changes, also read the repository-root `AGENTS.md`.

The dashboard is a client-rendered Next.js 15 App Router app (React, TypeScript, Radix UI Themes, Zustand, SWR, i18next). In Docker Compose the built UI is served by the Node API on port 3000, on the same origin as `/api` and `/mcp`. `npm run dev` from this directory serves the dashboard separately and calls the API on 3000; contributors usually run it as `PORT=3001 npm run dev`. Do not assume 3001 is where operators open the product.

## Commands

```bash
npm run dev                  # Next dev server (turbopack)
npm run build                # production build
npm run lint                 # next lint
npm run typecheck            # next typegen + tsc --noEmit
npm run test:unit            # vitest, all app/**/*.test.ts(x) and lib/**/*.test.ts(x)
npx vitest run lib/i18n/__tests__/formatters.test.ts   # one file
npm run i18n:check           # locale parity against en-US
npm run i18n:keys            # translation keys used in code exist
npm run test:e2e             # Playwright, tests/e2e (needs a running stack)
npm run test:e2e:smoke       # only @smoke-tagged specs
```

Unit tests live in `__tests__/` folders next to the code they test and run under jsdom.

## Where things live

- `app/(main)/` — signed-in app: `chat`, `agents`, `knowledge-base`, `workspace`, `connectors`, `notifications`, `projects`, `record`, `toolsets`. `@sidebar` is a parallel route for the sidebar slot.
- `app/(public)/` — unauthenticated pages: `login`, `sign-up`, `reset-password`, `oauth`, plus `api.ts` for public auth calls.
- `app/components/` — shared components; `app/components/ui/` holds primitives such as `MaterialIcon.tsx` and `Select.tsx`.
- `lib/api/` — HTTP client, SWR fetchers, SSE streaming, error types.
- `lib/store/` — global Zustand stores (`auth-store.ts`, `language-store.ts`, …).
- `lib/i18n/` — i18next config and `locales/`.
- `lib/socket/` — socket.io notification client.
- `docs/` — longer guides: `style-guide.md`, `i18n-guide.md`, `knowledge-base-architecture.md`, `url-driven-panel-state.md`.

## Naming: Collections vs Knowledge Base

The feature once called "Knowledge Base" is called "Collections" in the UI. Only user-facing text changed.

| Context | Name |
| --- | --- |
| UI labels and translations | "Collections", "All records" |
| Route | `/knowledge-base` |
| API | `/api/v1/knowledgeBase` |
| Code (types, stores, variables) | `KnowledgeBase`, `kb`, `kbId` |

The `/knowledge-base` page has two modes: Collections (folder tree) and All Records (`?view=all-records`, flat list across collections and connectors). Its components take a `pageViewMode` prop. URL state is parsed and serialized in `app/(main)/knowledge-base/url-params.ts`. See `docs/knowledge-base-architecture.md` before changing it.

## Routing

- There are no dynamic route segments. Identify entities with query params (`/chat?conversationId=…`, `/agents?agentId=…`, `/knowledge-base?nodeType=kb&nodeId=…`). The desktop build uses `output: 'export'` (`next.config.mjs`), which cannot emit per-id pages.
- Pages and components that use hooks or browser APIs start with `'use client'`.

## Page co-location

A feature folder under `app/(main)/<page>/` keeps its own `page.tsx`, `api.ts`, `store.ts`, `types.ts` and `components/` (with an `index.ts` barrel). Put page-specific code there; move it to `app/components/` or `lib/` only when a second page needs it.

File and folder names are kebab-case (`kb-data-table.tsx`), hooks are `use-*.ts`, components are PascalCase in code, constants are `SCREAMING_SNAKE_CASE`.

## Styling

- There is no Tailwind. Use Radix Themes layout and text components (`Flex`, `Box`, `Grid`, `Text`, `Heading`, `Card`) and their props (`gap="2"`, `size="2"`, `color="gray"`, responsive `columns={{ initial: '1', md: '2' }}`).
- For values outside the Radix scale, use inline `style` with CSS variables from `app/globals.css` (`var(--olive-3)`, `var(--accent-9)`, `var(--space-4)`, `var(--radius-2)`). Check `globals.css` before inventing a color.
- `className` is only for classes defined in `globals.css`: mainly `material-icons-outlined` (Material Icons font) and `no-scrollbar`, plus a few feature classes for animations and React Flow. Do not add utility-class strings.
- Hover and active states that Radix props cannot express are done with React state and inline styles.
- The theme is set once in `app/components/theme-provider.tsx` (accent `jade`, gray `olive`, radius `medium`, light/dark/system appearance). Do not wrap subtrees in another `<Theme>`.
- Icons: use `MaterialIcon` from `app/components/ui/`.
- Read `docs/style-guide.md` for Radix usage details.

## i18n

- Every user-facing string goes through `useTranslation()` and a key in `lib/i18n/locales/en-US.json`.
- There are 10 locales: `en-US` (source), `en-IN`, `de-DE`, `es-ES`, `hi-IN`, `ko-KR`, `zh-CN`, `zh-TW`, `zh-SG`, `cs-CZ`. `lib/i18n/locale-policy.json` gates all of them on parity with `en-US`, so a new key must be added to every locale file. Run `npm run i18n:check` and `npm run i18n:keys` after editing strings.
- Adding a language: follow `docs/i18n-guide.md` (locale file, `lib/i18n/supported-languages.ts`, `lib/i18n/locales/index.ts`).

## State

- Zustand. Page stores live in the page's `store.ts`; global stores in `lib/store/`. Most stores use the `immer` and `devtools` middleware; follow the store next to yours.
- `lib/store/auth-store.ts` persists tokens and is read by the API client. Use stores instead of prop drilling across more than a couple of levels.

## API and data fetching

- Components do not call axios directly. Each page exposes calls from its `api.ts` using `apiClient` from `@/lib/api` (`lib/api/axios-instance.ts`).
- `apiClient` adds the bearer token, refreshes on 401 and retries queued requests, converts failures to `ProcessedError` (`lib/api/api-error.ts`), and sends the user to `/login` when refresh fails. Handle auth and network errors there; handle business errors in the component.
- Unauthenticated calls use `publicAuthClient` (`lib/api/public-auth-client.ts`), as in `app/(public)/api.ts`.
- Reads use SWR with the fetchers in `lib/api/fetcher.ts`. Mutations with loading state and toasts use `useMutation` / `withToast` from `@/lib/api`.
- Chat responses stream over SSE with native `fetch` (`lib/api/streaming.ts`, `app/(main)/chat/streaming.ts`), because axios cannot stream. Notifications use socket.io (`lib/socket/notification-socket.ts`).

## Types

`tsconfig.json` has `"strict": false`, so the compiler will not catch nullish access for you. Guard optional values yourself, and do not add new `any`.

## Responsiveness

Support mobile widths (375–428px) through 1920px desktop. Use Radix responsive props, keep touch targets at least 44px, and collapse the sidebar on mobile.
