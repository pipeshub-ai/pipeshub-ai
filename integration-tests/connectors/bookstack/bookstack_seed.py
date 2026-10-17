"""What the BookStack tests seed, shared by the fixtures and the tests.

Pages are the connector's records; books and chapters are its record groups.
"""

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_DB_USER = "bookstack"
DEFAULT_DB_PASSWORD = "bookstack123"
DEFAULT_DB_NAME = "bookstack"

# Each run's books are named "<run folder> <title>".
HANDBOOK = "Handbook"
ENGINEERING = "Engineering"
POLICIES = "Policies"  # a chapter of the handbook

LEAVE_POLICY = ("Leave policy", "Everyone gets 25 days of paid leave a year.")
WELCOME = ("Welcome", "Welcome to the company handbook.")
RUNBOOK = ("Runbook", "Drain traffic, then restart the service.")
