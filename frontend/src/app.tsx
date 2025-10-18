import 'src/global.css';

// ----------------------------------------------------------------------

import { Provider } from 'react-redux';

import { Router } from 'src/routes/sections';

import { useScrollToTop } from 'src/hooks/use-scroll-to-top';

import { LocalizationProvider } from 'src/locales';
import { AdminProvider } from 'src/context/AdminContext';
import { I18nProvider } from 'src/locales/i18n-provider';
import { ThemeProvider } from 'src/theme/theme-provider';

import { Snackbar } from 'src/components/snackbar';
import { ProgressBar } from 'src/components/progress-bar';
import { MotionLazy } from 'src/components/animate/motion-lazy';
import { SettingsDrawer, defaultSettings, SettingsProvider } from 'src/components/settings';

import { AuthProvider as JwtAuthProvider } from 'src/auth/context/jwt';
import { ServicesHealthProvider } from 'src/context/ServicesHealthContext';
import { HealthGate } from 'src/components/guard/HealthGate';

import store from './store/store';
import { ErrorProvider } from './utils/axios';

// ----------------------------------------------------------------------

const AuthProvider = JwtAuthProvider;

export default function App() {
  useScrollToTop();

  return (
    <I18nProvider>
      <LocalizationProvider>
        <AuthProvider>
          <Provider store={store}>
            <SettingsProvider settings={defaultSettings}>
              <ThemeProvider>
                <AdminProvider>
                  <MotionLazy>
                    <Snackbar />
                    <ProgressBar />
                    <SettingsDrawer />
                    <ErrorProvider>
                      <ServicesHealthProvider>
                        <HealthGate>
                          <Router />
                        </HealthGate>
                      </ServicesHealthProvider>
                    </ErrorProvider>
                  </MotionLazy>
                </AdminProvider>
              </ThemeProvider>
            </SettingsProvider>
          </Provider>
        </AuthProvider>
      </LocalizationProvider>
    </I18nProvider>
  );
}
