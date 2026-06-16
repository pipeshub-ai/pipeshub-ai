"""
GitLab connector package (staging directory for the refactored connector).

After validation this directory will replace app/connectors/sources/gitlab/.
"""

from app.connectors.sources.gitlab1.connector import GitLabConnector
from app.connectors.sources.gitlab1.constants import GITLAB_CLOUD_URL

__all__ = ["GitLabConnector", "GITLAB_CLOUD_URL"]
