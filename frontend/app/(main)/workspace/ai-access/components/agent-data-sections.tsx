'use client';

import { isCpError, toCpError, type CpError } from '../../harness-ui/control-plane-api';

export const cpError = (caught: unknown, write = false): CpError => (isCpError(caught) ? caught : toCpError(caught, write));
