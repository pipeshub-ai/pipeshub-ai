/** Connectors config repository: which connector apps an organisation has switched on. */

export interface ConnectorsConfigDto {
  _id: string;
  orgId: string;
  name: string;
  isEnabled: boolean;
  lastUpdatedBy: string;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

export interface ConnectorsConfigRepository {
  find(orgId: string, name: string): Promise<ConnectorsConfigDto | null>;
  /** Switches the connector on and records who did; null when the organisation has no such connector. */
  enable(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null>;
  /** Adds the connector, switched on, and returns the stored row (null if it cannot be read back). */
  createEnabled(
    orgId: string,
    name: string,
    updatedBy: string,
  ): Promise<ConnectorsConfigDto | null>;
}
