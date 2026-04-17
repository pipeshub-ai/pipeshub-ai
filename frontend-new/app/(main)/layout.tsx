'use client'

import React, { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import "../globals.css"
import 'react-pdf-highlighter/dist/esm/style/PdfHighlighter.css';
import 'react-pdf-highlighter/dist/esm/style/Highlight.css';
import 'react-pdf-highlighter/dist/esm/style/AreaHighlight.css';
import 'react-pdf-highlighter/dist/esm/style/Tip.css';
import 'react-pdf-highlighter/dist/esm/style/MouseSelection.css';
import { Flex, Box, Text, IconButton } from "@radix-ui/themes"
import { MaterialIcon } from "../components/ui/MaterialIcon"
import { ThemeProvider, ThemeScript } from "../components/theme-provider"
import { SWRConfig } from "swr"
import { axiosFetcher } from "@/lib/api"
import { clearAuthAndRedirectToLogin } from "./auth-refresh"
import { UploadProgressTracker } from "../components/upload-progress-tracker"
import { ToastContainer } from "../components/feedback"
import { I18nextProvider, useTranslation } from 'react-i18next'
import i18n from '@/lib/i18n/config'
import { useLanguageStore } from '@/lib/store/language-store'
import { UserProfileInitializer } from './components/user-profile-initializer'
import { UserBackgroundSurvey } from "./components/surveys/user-background"
import { OnboardingTour } from "./components/tours/onboarding"
import { useOnboardingStore } from "./onboarding/store"
import { getOnboardingStatus } from "./onboarding/api"
import { useAuthStore } from "@/lib/store/auth-store"
import { useMobileSidebarStore } from "@/lib/store/mobile-sidebar-store"
import { useIsMobile } from "@/lib/hooks/use-is-mobile"
import { AuthGuard } from '@/app/components/ui/auth-guard'

export default function RootLayout({
  children,
  sidebar,
}: {
  children: React.ReactNode
  sidebar: React.ReactNode
}) {
  const [mounted, setMounted] = useState(false)
  const { t } = useTranslation()
  const language = useLanguageStore((state) => state.language)

  // Set mounted flag and sync i18n after hydration
  useEffect(() => {
    setMounted(true)
    if (language) {
      import('@/lib/i18n/config').then((module) => {
        module.default.changeLanguage(language);
      });
    }
  }, [language])

  useEffect(() => {
    document.title = "PipesHub"
  }, [])

  const currentLang = mounted ? language : 'en'

  // Auth enforcement: the <AuthGuard> component below handles the initial
  // auth check and redirects unauthenticated users to /login.
  // The axios interceptor in lib/api/axios-instance.ts also handles session
  // expiry at runtime: any 401 triggers a token refresh attempt; on failure
  // it calls logoutAndRedirect() which clears auth state and sends the user
  // to /login.
  return (
    <html lang={currentLang} suppressHydrationWarning>
      <head>
        <ThemeScript />
        <link
          href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined"
          rel="stylesheet"
        />
      </head>
      <body>
        <I18nextProvider i18n={i18n}>
          <ThemeProvider>
            {/* Landscape block — pure CSS visibility, no JS */}
            <div className="landscape-block-overlay">
              <MaterialIcon name="screen_rotation" size={48} color="var(--gray-11)" />
              <Text size="3" style={{ color: 'var(--gray-11)', maxWidth: '220px' }}>
                {t('common.rotateLandscape')}
              </Text>
            </div>
            <AuthGuard>
              <AppLayout sidebar={sidebar}>
                {children}
              </AppLayout>
            </AuthGuard>
          </ThemeProvider>
        </I18nextProvider>
      </body>
    </html>
  )
}

// Main app shell — auth is gated by <AuthGuard> (redirects to /login if not authenticated),
// with runtime session expiry handled by the axios interceptor (401 → refresh → redirect to login)
function AppLayout({
  children,
  sidebar,
}: {
  children: React.ReactNode
  sidebar: React.ReactNode
}) {
  const router = useRouter()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const isHydrated = useAuthStore((s) => s.isHydrated)
  const setOnboardingActive = useOnboardingStore((s) => s.setOnboardingActive)
  const openMobileSidebar = useMobileSidebarStore((s) => s.open)
  const isMobile = useIsMobile()

  // ── Onboarding gate ────────────────────────────────────────────────────────
  useEffect(() => {
    // 0. Already on the onboarding page — bail out to prevent an infinite redirect loop
    if (window.location.pathname.startsWith('/onboarding')) {
      return
    }

    // 1. Not yet hydrated or not authenticated — too early to check
    if (!isHydrated || !isAuthenticated) {
      return
    }

    // 2. Call the API and decide
    const forceActive = process.env.NEXT_PUBLIC_FORCE_ONBOARDING_ACTIVE === 'true'

    getOnboardingStatus()
      .then(({ status }) => {
        if (status === 'notConfigured' || forceActive) {
          setOnboardingActive(true)
          router.replace('/onboarding')
        }
      })
      .catch(() => {
        // If the check fails (network, permissions) proceed normally — don't block the app
      })
  }, [isHydrated, isAuthenticated])
  // ──────────────────────────────────────────────────────────────────────────

  return (
    <SWRConfig
      value={{
        fetcher: axiosFetcher,
        onError: (error) => {
          if (error?.type === 'AUTHENTICATION_ERROR') {
            clearAuthAndRedirectToLogin(router)
          }
        },
      }}
    >
      {/* Hydrates user profile (name, email, isAdmin, avatar) once auth is ready */}
      <UserProfileInitializer />
      <Flex
        style={{
          height: '100vh',
          width: '100%',
          overflow: 'hidden',
          backgroundColor: 'var(--slate-2)',
        }}
      >
        {/* Sidebar slot — on desktop renders inline; on mobile the sidebar
            component itself renders as a fixed overlay (controlled via
            useMobileSidebarStore). Nothing is rendered here on mobile. */}
        <React.Fragment key="app-sidebar-slot">{sidebar}</React.Fragment>

        {/* Main content area — zIndex: 0 creates a stacking context so
            page-internal z-indexes don't compete with the sidebar's
            secondary panel (zIndex: 10 in root context). */}
        <Flex
          key="app-main-column"
          direction="column"
          data-main-content
          style={{ flex: 1, overflow: 'hidden', zIndex: 0, position: 'relative' }}
        >
          {/* Mobile hamburger — fixed top-left, only visible on mobile.
              Uses position:fixed (not absolute) so it sits in the root stacking
              context above the chat page content elements that also use zIndex:10. */}
          {isMobile && (
            <Box
              key="app-mobile-menu-anchor"
              style={{
                position: 'fixed',
                top: 'var(--space-3)',
                left: 'var(--space-3)',
                zIndex: 100,
              }}
            >
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={openMobileSidebar}
                style={{ margin: 0 }}
                aria-label="Open sidebar"
              >
                <MaterialIcon name="menu" size={22} color="var(--gray-11)" />
              </IconButton>
            </Box>
          )}
          <Box
            key="app-main-scroll"
            data-app-main-scroll
            className="no-scrollbar"
            style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}
          >
            {children}
          </Box>
        </Flex>

        <UploadProgressTracker key="upload-progress-tracker" />
        <ToastContainer key="toast-container" />
        {/* User background survey — shown once after login/onboarding */}
        <UserBackgroundSurvey key="user-background-survey" />
        {/* Onboarding tour card — bottom-left corner, guides new users through first steps.
             Currently gated by the NEXT_PUBLIC_ONBOARDING_TOUR_ACTIVE env var.
             TODO: replace the env-var flag with an API call (similar to the onboarding flow above):
               1. Call getTourStatus() on mount.
               2. Mount <OnboardingTour /> when the response status is 'active' or 'completed'.
               3. Omit it entirely when status is 'hidden' (user has dismissed). */}
        {process.env.NEXT_PUBLIC_ONBOARDING_TOUR_ACTIVE === 'true' ? (
          <OnboardingTour key="onboarding-tour" />
        ) : null}
      </Flex>
    </SWRConfig>
  )
}
