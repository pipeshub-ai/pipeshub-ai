'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { AuthMethod } from '../api';
import SingleProvider from './single-provider';
import AuthTitleSection from '../components/auth-title-section';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface MultipleProvidersProps {
  /** Auth methods returned by initAuth (always 2+). */
  allowedMethods: AuthMethod[];
  /** Provider-specific config (redirect URLs etc.). */
  authProviders: Record<string, Record<string, string>>;
  /** Go back to the email step. */
  onBack?: () => void;
}

// ─── Tab metadata ─────────────────────────────────────────────────────────────

interface TabConfig {
  method: AuthMethod;
  label: string;
  icon: string;
  iconType: 'material' | 'image';
}

function getTabConfig(
  method: AuthMethod,
  authProviders: Record<string, Record<string, string>>,
): TabConfig {
  switch (method) {
    case 'password':
      return {
        method,
        label: 'Password',
        icon: '/icons/auth-config/lock-closed.svg',
        iconType: 'image',
      };
    case 'otp':
      return { method, label: 'Email OTP', icon: 'mail_lock', iconType: 'material' };
    case 'samlSso':
      const providerName = authProviders?.saml?.samlPlatform ?? 'SSO';
      return { method, label: 'SSO', icon: 'security', iconType: 'material' };
    case 'google':
      return {
        method,
        label: 'Google',
        icon: '/icons/auth-config/google.svg',
        iconType: 'image',
      };
    case 'microsoft':
    case 'azureAd':
      return {
        method,
        label: 'Microsoft',
        icon: '/icons/auth-config/windows.svg',
        iconType: 'image',
      };
    case 'oauth': {
      const providerName = authProviders?.oauth?.providerName ?? 'OAuth';
      return { method, label: "OAuth", icon: 'vpn_key', iconType: 'material' };
    }
    default:
      return { method, label: method, icon: 'login', iconType: 'material' };
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * MultipleProviders — tabbed layout for 2+ auth methods.
 *
 * Renders a tab strip where each enabled auth method is a tab. Clicking a tab
 * switches to that method's form, which behaves identically to SingleProvider
 * for the same method.
 */
export default function MultipleProviders({
  allowedMethods,
  authProviders,
  onBack,
}: MultipleProvidersProps) {
  const [activeMethod, setActiveMethod] = useState<AuthMethod>(allowedMethods[0]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const tabs = allowedMethods.map((method) => getTabConfig(method, authProviders));

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollState();
    el.addEventListener('scroll', updateScrollState);
    const ro = new ResizeObserver(updateScrollState);
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', updateScrollState);
      ro.disconnect();
    };
  }, [updateScrollState]);

  const scrollBy = (direction: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: direction === 'left' ? -120 : 120, behavior: 'smooth' });
  };

  const arrowButtonStyle = (side: 'left' | 'right'): React.CSSProperties => ({
    position: 'absolute',
    top: 0,
    bottom: 0,
    [side]: 0,
    zIndex: 1,
    display: 'flex',
    alignItems: 'center',
    padding: '0 6px',
    border: 'none',
    cursor: 'pointer',
    background:
      side === 'left'
        ? 'linear-gradient(to right, var(--olive-3) 55%, transparent)'
        : 'linear-gradient(to left, var(--olive-3) 55%, transparent)',
    borderRadius:
      side === 'left'
        ? 'var(--radius-2) 0 0 var(--radius-2)'
        : '0 var(--radius-2) var(--radius-2) 0',
    color: 'var(--slate-11)',
  });

  return (
    <Box style={{ width: '100%', maxWidth: '440px' }}>
      {/* ── Title ── */}
      <AuthTitleSection />

      {/* ── Tab strip ── */}
      <Box style={{ position: 'relative', marginBottom: 'var(--space-5)' }}>
        {canScrollLeft && (
          <button type="button" onClick={() => scrollBy('left')} style={arrowButtonStyle('left')}>
            <span className="material-icons-outlined" style={{ fontSize: 16 }}>
              chevron_left
            </span>
          </button>
        )}

        <Flex
          ref={scrollRef}
          gap="1"
          className="no-scrollbar"
          style={{
            padding: '4px',
            backgroundColor: 'var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            overflowX: 'auto',
            flexWrap: 'nowrap',
          }}
        >
          {tabs.map((tab) => {
            const isActive = activeMethod === tab.method;
            return (
              <button
                key={tab.method}
                type="button"
                onClick={() => setActiveMethod(tab.method)}
                style={{
                  flexShrink: 0,
                  flex: !canScrollLeft && !canScrollRight ? 1 : '0 0 auto',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  padding: '6px 10px',
                  borderRadius: 'var(--radius-1)',
                  border: 'none',
                  cursor: 'pointer',
                  background: isActive ? 'var(--olive-1)' : 'transparent',
                  color: isActive ? 'var(--slate-12)' : 'var(--slate-10)',
                  transition: 'background 120ms ease, color 120ms ease',
                  boxShadow: isActive ? '0 1px 3px var(--slate-a4)' : 'none',
                  whiteSpace: 'nowrap',
                }}
              >
                {tab.iconType === 'image' ? (
                  <img
                    src={tab.icon}
                    alt=""
                    style={{ width: 14, height: 14, objectFit: 'contain', flexShrink: 0 }}
                  />
                ) : (
                  <span
                    className="material-icons-outlined"
                    style={{ fontSize: 14, flexShrink: 0 }}
                  >
                    {tab.icon}
                  </span>
                )}
                <Text
                  size="1"
                  weight={isActive ? 'medium' : 'regular'}
                  style={{ color: 'inherit' }}
                >
                  {tab.label}
                </Text>
              </button>
            );
          })}
        </Flex>

        {canScrollRight && (
          <button type="button" onClick={() => scrollBy('right')} style={arrowButtonStyle('right')}>
            <span className="material-icons-outlined" style={{ fontSize: 16 }}>
              chevron_right
            </span>
          </button>
        )}
      </Box>

      {/* ── Active method form — remounts on tab switch to reset internal state ── */}
      <SingleProvider
        key={activeMethod}
        method={activeMethod}
        authProviders={authProviders}
        onBack={onBack}
        hideTitle
      />
    </Box>
  );
}
