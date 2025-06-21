class Etcd3Backup:
    def __init__(self, client, backup_path: str):
        self.client = client
        self.backup_path = backup_path

    async def create_snapshot(self):
        # Create backup snapshot
        pass

    async def restore_snapshot(self, snapshot_path: str):
        # Restore from snapshot
        pass
