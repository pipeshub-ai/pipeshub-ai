"""
GitLab connector package (staging directory for the refactored connector).

After validation this directory will replace app/connectors/sources/gitlab/.
"""

from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab.constants import GITLAB_CLOUD_URL

__all__ = ["GitLabConnector", "GITLAB_CLOUD_URL"]
