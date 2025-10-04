"""
Docker helper utilities for integration tests.
"""
import time
import subprocess
from typing import Dict, List, Optional, Tuple
from docker import DockerClient
from docker.errors import ContainerError, NotFound


class DockerHelper:
    """Helper class for Docker container management during tests."""
    
    def __init__(self):
        self.client = DockerClient()
        self.containers = {}
    
    def start_test_containers(self, containers: List[str] = None) -> Dict[str, bool]:
        """Start specified test containers."""
        if containers is None:
            containers = ["redis", "mongodb", "arangodb", "qdrant"]
        
        results = {}
        for container_name in containers:
            try:
                result = self._start_container(container_name)
                results[container_name] = result
            except Exception as e:
                print(f"Failed to start {container_name}: {e}")
                results[container_name] = False
        
        return results
    
    def stop_test_containers(self, containers: List[str] = None):
        """Stop specified test containers."""
        if containers is None:
            containers = list(self.containers.keys())
        
        for container_name in containers:
            if container_name in self.containers:
                try:
                    self.containers[container_name].stop(timeout=5)
                    print(f"Stopped {container_name}")
                except Exception as e:
                    print(f"Failed to stop {container_name}: {e}")
    
    def cleanup_test_containers(self):
        """Remove all test containers."""
        for container_name, container in self.containers.items():
            try:
                container.remove(force=True)
                print(f"Removed {container_name}")
            except Exception as e:
                print(f"Failed to remove {container_name}: {e}")
    
    def get_container_status(self, container_name: str) -> Optional[str]:
        """Get the status of a container."""
        if container_name in self.containers:
            try:
                self.containers[container_name].reload()
                return self.containers[container_name].status
            except Exception:
                return None
        return None
    
    def wait_for_container_health(self, container_name: str, timeout: int = 30) -> bool:
        """Wait for a container to be healthy."""
        if container_name not in self.containers:
            return False
        
        container = self.containers[container_name]
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                container.reload()
                if container.status == "running":
                    if self._check_container_health(container_name):
                        return True
                time.sleep(2)
            except Exception:
                time.sleep(2)
        
        return False
    
    def _start_container(self, container_name: str) -> bool:
        """Start a specific container."""
        container_configs = {
            "redis": {
                "image": "redis:bookworm",
                "ports": {"6379/tcp": 6379},
                "name": "pipeshub-test-redis",
                "environment": {},
            },
            "mongodb": {
                "image": "mongo:8.0.6",
                "ports": {"27017/tcp": 27017},
                "name": "pipeshub-test-mongodb",
                "environment": {
                    "MONGO_INITDB_ROOT_USERNAME": "admin",
                    "MONGO_INITDB_ROOT_PASSWORD": "password",
                },
            },
            "arangodb": {
                "image": "arangodb:3.12.4",
                "ports": {"8529/tcp": 8529},
                "name": "pipeshub-test-arangodb",
                "environment": {"ARANGO_ROOT_PASSWORD": "test_password"},
            },
            "qdrant": {
                "image": "qdrant/qdrant:v1.13.6",
                "ports": {"6333/tcp": 6333, "6334/tcp": 6334},
                "name": "pipeshub-test-qdrant",
                "environment": {"QDRANT__SERVICE__API_KEY": "test_qdrant_key"},
            },
        }
        
        if container_name not in container_configs:
            raise ValueError(f"Unknown container: {container_name}")
        
        config = container_configs[container_name]
        
        # Remove existing container if it exists
        try:
            existing = self.client.containers.get(config["name"])
            existing.remove(force=True)
        except NotFound:
            pass
        
        # Start new container
        container = self.client.containers.run(
            config["image"],
            name=config["name"],
            ports=config["ports"],
            environment=config["environment"],
            detach=True,
            remove=True,
        )
        
        self.containers[container_name] = container
        return True
    
    def _check_container_health(self, container_name: str) -> bool:
        """Check if a container is healthy."""
        health_checks = {
            "redis": self._check_redis_health,
            "mongodb": self._check_mongodb_health,
            "arangodb": self._check_arangodb_health,
            "qdrant": self._check_qdrant_health,
        }
        
        if container_name in health_checks:
            return health_checks[container_name]()
        return True
    
    def _check_redis_health(self) -> bool:
        """Check if Redis is healthy."""
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=1)
            r.ping()
            return True
        except Exception:
            return False
    
    def _check_mongodb_health(self) -> bool:
        """Check if MongoDB is healthy."""
        try:
            import pymongo
            client = pymongo.MongoClient(
                "mongodb://admin:password@localhost:27017/",
                serverSelectionTimeoutMS=1000
            )
            client.server_info()
            return True
        except Exception:
            return False
    
    def _check_arangodb_health(self) -> bool:
        """Check if ArangoDB is healthy."""
        try:
            import httpx
            response = httpx.get("http://localhost:8529/_api/version", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
    
    def _check_qdrant_health(self) -> bool:
        """Check if Qdrant is healthy."""
        try:
            import httpx
            response = httpx.get("http://localhost:6333/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


class ServiceManager:
    """Manages PipesHub AI services during testing."""
    
    def __init__(self):
        self.processes = {}
    
    def start_services(self, services: List[str] = None) -> Dict[str, bool]:
        """Start specified services."""
        if services is None:
            services = ["nodejs_backend", "query_service", "indexing_service", "connector_service"]
        
        results = {}
        for service_name in services:
            try:
                result = self._start_service(service_name)
                results[service_name] = result
            except Exception as e:
                print(f"Failed to start {service_name}: {e}")
                results[service_name] = False
        
        return results
    
    def stop_services(self, services: List[str] = None):
        """Stop specified services."""
        if services is None:
            services = list(self.processes.keys())
        
        for service_name in services:
            if service_name in self.processes:
                try:
                    self.processes[service_name].terminate()
                    self.processes[service_name].wait(timeout=5)
                    print(f"Stopped {service_name}")
                except Exception as e:
                    print(f"Failed to stop {service_name}: {e}")
    
    def _start_service(self, service_name: str) -> bool:
        """Start a specific service."""
        service_commands = {
            "nodejs_backend": ["npm", "run", "dev"],
            "query_service": ["python", "-m", "app.query_main"],
            "indexing_service": ["python", "-m", "app.indexing_main"],
            "connector_service": ["python", "-m", "app.connectors_main"],
        }
        
        if service_name not in service_commands:
            raise ValueError(f"Unknown service: {service_name}")
        
        command = service_commands[service_name]
        
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._get_service_directory(service_name)
            )
            
            self.processes[service_name] = process
            return True
        except Exception as e:
            print(f"Failed to start {service_name}: {e}")
            return False
    
    def _get_service_directory(self, service_name: str) -> str:
        """Get the working directory for a service."""
        directories = {
            "nodejs_backend": "backend/nodejs/apps",
            "query_service": "backend/python",
            "indexing_service": "backend/python",
            "connector_service": "backend/python",
        }
        
        return directories.get(service_name, ".")


def check_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        client = DockerClient()
        client.ping()
        return True
    except Exception:
        return False


def check_required_ports() -> Dict[str, bool]:
    """Check if required ports are available."""
    import socket
    
    ports = {
        "redis": 6379,
        "mongodb": 27017,
        "arangodb": 8529,
        "qdrant": 6333,
        "nodejs_backend": 3000,
        "query_service": 8000,
        "indexing_service": 8091,
        "connector_service": 8088,
    }
    
    results = {}
    for service, port in ports.items():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            results[service] = result == 0
        except Exception:
            results[service] = False
    
    return results

