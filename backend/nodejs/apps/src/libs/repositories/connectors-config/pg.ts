/** PostgreSQL implementation of the connectors-config repository (`DOC_STORE=postgres`): one row per connector name in an org. */
import { BadRequestError } from '../../errors/http.errors';
import { stripNul } from '../nul';
import { assertOrg, fill, inUnit, newObjectId, oidOf } from '../pg-util';
import type { Tx as PgKernelTx } from '../../db';
import type { ConnectorsConfigDto, ConnectorsConfigRepository } from './types';

interface Row {
  id: string;
  org_id: string;
  name: string;
  is_enabled: boolean;
  last_updated_by: string;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS =
  'id, org_id, name, is_enabled, last_updated_by, version, created_at, updated_at';

const dto = (r: Row): ConnectorsConfigDto => ({
  _id: r.id,
  orgId: r.org_id,
  name: r.name,
  isEnabled: r.is_enabled,
  lastUpdatedBy: r.last_updated_by,
  createdAt: r.created_at,
  updatedAt: r.updated_at,
  __v: r.version,
});

const author = (id: string): string => {
  const oid = oidOf(id);
  if (!oid)
    throw new BadRequestError(
      'the user of a connector change must be a valid user id',
    );
  return oid;
};

async function read(
  k: PgKernelTx,
  name: string,
): Promise<ConnectorsConfigDto | null> {
  const { rows } = await k.query<Row>(
    fill(
      '/* pipeshub:platform.connectors_config_get */ SELECT @{COLS} FROM platform.connectors_config WHERE org_id = $1 AND name = $2',
      { COLS },
    ),
    [k.orgId, name],
  );
  return rows[0] ? dto(rows[0]) : null;
}

export class PgConnectorsConfigRepository implements ConnectorsConfigRepository {
  async find(orgId: string, name: string): Promise<ConnectorsConfigDto | null> {
    return inUnit(undefined, { write: false }, (k) => {
      assertOrg(k, orgId);
      return read(k, stripNul(name, 'connectors-config', 'name'));
    });
  }

  async enable(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null> {
    const by = author(updatedBy);
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:platform.connectors_config_enable */ UPDATE platform.connectors_config SET is_enabled = true, last_updated_by = $3, updated_at = $4
          WHERE org_id = $1 AND name = $2 RETURNING @{COLS}`,
          { COLS },
        ),
        [k.orgId, stripNul(name, 'connectors-config', 'name'), by, new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async createEnabled(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null> {
    const by = author(updatedBy);
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const n = stripNul(name, 'connectors-config', 'name');
      const now = new Date();
      await k.query(
        `/* pipeshub:platform.connectors_config_create */ INSERT INTO platform.connectors_config (org_id, name, id, is_enabled, last_updated_by, created_at, updated_at)
           VALUES ($1, $2, $3, true, $4, $5, $5) ON CONFLICT DO NOTHING`,
        [k.orgId, n, newObjectId(), by, now],
      );
      return read(k, n);
    });
  }
}
