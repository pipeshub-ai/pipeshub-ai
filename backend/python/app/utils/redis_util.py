from app.config.constants.service import RedisConfig


def build_redis_url(redis_config: dict) -> str:
    """Builds a Redis connection URL from a configuration dictionary."""
    host = redis_config.get('host', 'localhost')
    port = redis_config.get('port', 6379)
    username = redis_config.get('username')
    password = redis_config.get('password')
    db = redis_config.get('db', RedisConfig.REDIS_DB.value)
    tls = redis_config.get('tls', False)

    # Use rediss:// scheme for TLS (equivalent to redis-cli --tls)
    scheme = "rediss" if tls else "redis"

    # Build auth part of URL
    if username and username.strip() and password and password.strip():
        return f"{scheme}://{username}:{password}@{host}:{port}/{db}"
    elif password and password.strip():
        return f"{scheme}://:{password}@{host}:{port}/{db}"
    return f"{scheme}://{host}:{port}/{db}"
