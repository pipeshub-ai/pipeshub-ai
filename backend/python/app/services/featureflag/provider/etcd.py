from typing import Optional

from app.services.featureflag.interfaces.config import IConfigProvider


class EtcdProvider(IConfigProvider):
    """
    FUTURE: Provider for etcd configuration service
    Usage:
        provider = EtcdProvider(host='localhost', port=2379, prefix='/feature_flags')
    """
    def get_flag_value(self, flag_name: str) -> Optional[bool]:
        raise NotImplementedError("EtcdProvider is not implemented")

    def refresh(self) -> None:
        raise NotImplementedError("EtcdProvider is not implemented")
