class Etcd3Backup:
    def __init__(self, client, backup_path: str) -> None:
        self.client = client
        self.backup_path = backup_path

    async def create_snapshot(self) -> None:
        # Create backup snapshot
        pass

    async def restore_snapshot(self, snapshot_path: str) -> None:
        # Restore from snapshot
        pass
