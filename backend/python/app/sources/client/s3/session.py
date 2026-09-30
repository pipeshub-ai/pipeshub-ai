from typing import Optional

from aiobotocore.session import ClientCreatorContext, get_session


class AioBotoSession:
    """Drop-in for the ``aioboto3.Session().client(...)`` pattern on top of aiobotocore.

    aioboto3 pins boto3 to an old range, which blocks security upgrades of other
    boto3 consumers (e.g. redshift-connector). Only ``client()`` was ever used.
    """

    def __init__(
        self,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> None:
        self._session = get_session()
        self._client_kwargs = {
            key: value
            for key, value in (
                ("aws_access_key_id", aws_access_key_id),
                ("aws_secret_access_key", aws_secret_access_key),
                ("aws_session_token", aws_session_token),
                ("region_name", region_name),
            )
            if value is not None
        }

    def client(self, service_name: str, **kwargs: object) -> ClientCreatorContext:
        return self._session.create_client(service_name, **{**self._client_kwargs, **kwargs})
