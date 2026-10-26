'use client';

import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import i18n from '@/lib/i18n/config';
import {
  DEFAULT_BRAND,
  docsHref as joinDocsHref,
  i18nBrandVariables,
  type PublicBrand,
} from './defaults';

export interface BrandContextValue extends PublicBrand {
  isDefaultProduct: boolean;
  docsHref: (path?: string) => string | null;
}

export interface BrandProviderProps {
  children: React.ReactNode;
  /** Where to get the brand from. Without it the default brand is used and nothing is requested. */
  loadBrand?: () => Promise<PublicBrand>;
}

const BrandContext = createContext<BrandContextValue | null>(null);

function applyI18nBrand(brand: PublicBrand): void {
  const interpolation = i18n.options.interpolation ?? {};
  i18n.options.interpolation = {
    ...interpolation,
    defaultVariables: i18nBrandVariables(brand),
  };
}

function toContextValue(brand: PublicBrand): BrandContextValue {
  return {
    ...brand,
    isDefaultProduct: brand.productName === DEFAULT_BRAND.productName,
    docsHref: (path?: string) => joinDocsHref(brand.docsUrl, path),
  };
}

applyI18nBrand(DEFAULT_BRAND);

export function BrandProvider({ children, loadBrand }: BrandProviderProps) {
  const [brand, setBrand] = useState<PublicBrand>(DEFAULT_BRAND);

  useEffect(() => {
    if (!loadBrand) return;
    let cancelled = false;
    loadBrand()
      .then((next) => {
        if (cancelled) return;
        applyI18nBrand(next);
        setBrand(next);
        document.title = next.productName;
      })
      .catch(() => {
        /* keep defaults so login still renders if the brand cannot be loaded */
      });
    return () => {
      cancelled = true;
    };
  }, [loadBrand]);

  const value = useMemo(() => toContextValue(brand), [brand]);

  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): BrandContextValue {
  const value = useContext(BrandContext);
  if (!value) {
    return toContextValue(DEFAULT_BRAND);
  }
  return value;
}
