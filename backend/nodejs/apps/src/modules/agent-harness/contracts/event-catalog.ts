import type { ZodTypeAny } from 'zod/v3';
import {
  EVENT_SCHEMAS as CORE_EVENT_SCHEMAS,
  EVENT_WRITERS as CORE_EVENT_WRITERS,
  EventWriter,
} from './generated/core';
import {
  EVENT_SCHEMAS as EXT_EVENT_SCHEMAS,
  EVENT_WRITERS as EXT_EVENT_WRITERS,
} from './generated/ext/pipeshub';

export type { EventWriter };

/** Every journal event type this build can read or write (core + x.pipeshub). */
export const JOURNAL_EVENT_SCHEMAS: Readonly<Record<string, ZodTypeAny>> =
  Object.freeze({ ...CORE_EVENT_SCHEMAS, ...EXT_EVENT_SCHEMAS });

/** Who may append each type (REPORT 6.1.2 writer column). */
export const JOURNAL_EVENT_WRITERS: Readonly<
  Record<string, readonly EventWriter[]>
> = Object.freeze({ ...CORE_EVENT_WRITERS, ...EXT_EVENT_WRITERS });
