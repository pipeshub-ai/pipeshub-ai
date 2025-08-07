class DriveWorker:
    """Worker class for processing individual drives"""

    def __init__(self, drive_id: str, drive_service, arango_service) -> None:
        self.drive_id = drive_id
        self.drive_service = drive_service
        self.arango_service = arango_service
        self.is_processing = False
        self.files_processed = 0
