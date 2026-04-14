'use client';

import React, { useEffect, useState } from 'react';
import "../globals.css";
import { I18nextProvider } from 'react-i18next';
import i18n from '@/lib/i18n/config';
import { useLanguageStore } from '@/lib/store/language-store';
import { ToastContainer } from '@/app/components/feedback';
import { ThemeProvider, ThemeScript } from '@/app/components/theme-provider';
import { AuthHydrator } from '@/lib/store/auth-hydrator';
import { ServerUrlGuard } from '@/app/components/electron/server-url-setup';

export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [mounted, setMounted] = useState(false);
  const language = useLanguageStore((state) => state.language);

  useEffect(() => {
    setMounted(true);
    document.title = 'PipesHub';
    if (language) {
      import('@/lib/i18n/config').then((module) => {
        module.default.changeLanguage(language);
      });
    }
  }, [language]);

  const currentLang = mounted ? language : 'en';

  return (
    <html lang={currentLang} suppressHydrationWarning>
      <head>
        <ThemeScript />
        <link
          href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined|Material+Icons"
          rel="stylesheet"
        />
      </head>
      <body>
        <I18nextProvider i18n={i18n}>
          <ThemeProvider>
            <AuthHydrator />
            <ServerUrlGuard>{children}</ServerUrlGuard>
            <ToastContainer />
          </ThemeProvider>
        </I18nextProvider>
      </body>
    </html>
  );
}

