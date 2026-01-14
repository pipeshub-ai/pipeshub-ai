export enum StoreType {
  Etcd3 = 'etcd3',
  InMemory = 'inmemory',
  Redis = 'redis',
  // Add other backend types as needed
}

export class KeyValueStoreType {
  static fromString(storeType: string): StoreType {
    switch (storeType) {
      case 'etcd3':
        return StoreType.Etcd3;
      case 'inmemory':
        return StoreType.InMemory;
      case 'redis':
        return StoreType.Redis;
      default:
        throw new Error(`Unsupported store type: ${storeType}`);
    }
  }

  static toString(storeType: StoreType): string {
    return storeType;
  }
}
