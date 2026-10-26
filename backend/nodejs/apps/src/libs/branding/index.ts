import { BrandConfig, DEFAULT_BRAND } from './defaults';

export { DEFAULT_BRAND } from './defaults';
export type { BrandConfig } from './defaults';

let resolveBrand: () => BrandConfig = () => DEFAULT_BRAND;

/** Replaces where the product name, support address and links come from. */
export function setBrandResolver(fn: () => BrandConfig): void {
  resolveBrand = fn;
}

export function getBrandConfig(): BrandConfig {
  return resolveBrand();
}
