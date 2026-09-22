"""What the Nextcloud tests seed, shared by the fixtures and the tests."""

# Admin defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_ADMIN_USER = "pipeshubadmin"
DEFAULT_ADMIN_PASSWORD = "pipeshub-nc-admin-123"
SYNC_USER = "pipeshub-it"
SYNC_PASSWORD = "pipeshub-it-Pass-2026!"

# The run's files, relative to the run folder. With the run folder and the
# Handbook folder that is five records.
SEED_FILES = {
    "Handbook/leave-policy.txt": "Everyone gets 25 days of paid leave a year.",
    "Handbook/onboarding.md": "# Onboarding\n\nRead the handbook in your first week.",
    "notes.txt": "Standup is at 10:00 every weekday.",
}
SEED_FOLDERS = ["Handbook"]
