import { ConnectorsConfig } from '../../../modules/configuration_manager/schema/connectors.schema';
import { ConnectorNames } from '../../types/connector.types';
import { toPlain } from '../plain';
import type { ConnectorsConfigDto, ConnectorsConfigRepository } from './types';

// The stored name is validated by the caller; the schema types it as the enum.
const asConnector = (name: string): ConnectorNames =>
  // eslint-disable-next-line @typescript-eslint/no-unsafe-enum-assignment
  name as ConnectorNames;

const dto = (row: unknown): ConnectorsConfigDto =>
  toPlain<ConnectorsConfigDto>(row);

export class MongoConnectorsConfigRepository implements ConnectorsConfigRepository {
  async find(orgId: string, name: string): Promise<ConnectorsConfigDto | null> {
    const row = await ConnectorsConfig.findOne({
      name: asConnector(name),
      orgId,
    });
    return row ? dto(row) : null;
  }

  async enable(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null> {
    const connector = await ConnectorsConfig.findOne({
      name: asConnector(name),
      orgId,
    });
    if (!connector) return null;
    connector.isEnabled = true;
    connector.lastUpdatedBy =
      updatedBy as unknown as typeof connector.lastUpdatedBy;
    await connector.save();
    return dto(connector);
  }

  async createEnabled(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null> {
    await new ConnectorsConfig({
      orgId,
      name: asConnector(name),
      lastUpdatedBy: updatedBy,
      isEnabled: true,
    }).save();
    const row = await ConnectorsConfig.findOne({
      name: asConnector(name),
      orgId,
    });
    return row ? dto(row) : null;
  }
}
