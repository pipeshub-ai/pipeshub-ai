import type { TFunction } from 'i18next';
import type { AppServices } from '@/lib/store/services-health-store';
import type { ReasonedServiceMeta } from './service-reason';

/** A row the Services page adds after its own, from the health answer; null: no row. */
export type ServiceRowExtra = (t: TFunction, appServices: AppServices | null) => ReasonedServiceMeta | null;

/** Rows an edition adds to the Services page, through config.ts. This build adds none. */
export const serviceRowExtras: readonly ServiceRowExtra[] = [];
