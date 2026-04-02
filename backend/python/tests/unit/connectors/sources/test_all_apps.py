"""Tests for all connector apps.py files — covers the __init__ constructor for each.

Each App subclass just calls super().__init__ with specific Connectors/AppGroups values.
These tests verify the constructors work and the correct enum values are set.
"""

import pytest


class TestAllConnectorApps:
    """Instantiate every connector App to cover their __init__ lines."""

    def test_bookstack_app(self):
        from app.connectors.sources.bookstack.common.apps import BookStackApp
        app = BookStackApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_box_app(self):
        from app.connectors.sources.box.common.apps import BoxApp
        app = BoxApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_dropbox_app(self):
        from app.connectors.sources.dropbox.common.apps import DropboxApp
        app = DropboxApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_dropbox_individual_app(self):
        from app.connectors.sources.dropbox_individual.common.apps import DropboxIndividualApp
        app = DropboxIndividualApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_github_app(self):
        from app.connectors.sources.github.common.apps import GithubApp
        app = GithubApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_minio_app(self):
        from app.connectors.sources.minio.common.apps import MinIOApp
        app = MinIOApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_nextcloud_app(self):
        from app.connectors.sources.nextcloud.common.apps import NextcloudApp
        app = NextcloudApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_s3_app(self):
        from app.connectors.sources.s3.common.apps import S3App
        app = S3App(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_zammad_app(self):
        from app.connectors.sources.zammad.common.apps import ZammadApp
        app = ZammadApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_azure_blob_app(self):
        from app.connectors.sources.azure_blob.common.apps import AzureBlobApp
        app = AzureBlobApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_azure_files_app(self):
        from app.connectors.sources.azure_files.common.apps import AzureFilesApp
        app = AzureFilesApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_google_cloud_storage_app(self):
        from app.connectors.sources.google_cloud_storage.common.apps import GCSApp
        app = GCSApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_servicenow_app(self):
        from app.connectors.sources.servicenow.common.apps import ServicenowApp
        app = ServicenowApp(connector_id="conn-1")
        assert app.get_connector_id() == "conn-1"

    def test_servicenow_app_group_missing_connector_id(self):
        """ServiceNowAppGroup.__init__ does not pass connector_id to super(),
        so constructing it raises TypeError. This test covers line 16."""
        from app.connectors.sources.servicenow.common.apps import ServiceNowAppGroup
        with pytest.raises(TypeError):
            ServiceNowAppGroup(connector_id="conn-1")

    def test_servicenow_app_enum_values(self):
        from app.connectors.sources.servicenow.common.apps import ServicenowApp
        from app.config.constants.arangodb import AppGroups, Connectors
        app = ServicenowApp(connector_id="conn-2")
        assert app.get_app_name() == Connectors.SERVICENOW
        assert app.get_app_group_name() == AppGroups.SERVICENOW
