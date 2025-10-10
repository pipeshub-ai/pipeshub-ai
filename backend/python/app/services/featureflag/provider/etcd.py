from app.services.featureflag.interfaces.config import IConfigProvider


class EtcdProvider(IConfigProvider):
    """
    FUTURE: Provider for etcd configuration service
    Usage:
        provider = EtcdProvider(host='localhost', port=2379, prefix='/feature_flags')
    """
    raise NotImplementedError("EtcdProvider is not implemented")
