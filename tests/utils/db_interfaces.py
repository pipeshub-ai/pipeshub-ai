"""
Database interfaces for integration tests.

Provides wrapper classes for interacting with MongoDB, ArangoDB, Qdrant, and Redis
during integration tests.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import pymongo  # type: ignore
from arango import ArangoClient  # type: ignore
from qdrant_client import QdrantClient  # type: ignore
from redis import Redis  # type: ignore

from tests.config.settings import get_settings

logger = logging.getLogger(__name__)


class MongoInterface:
    """Interface for MongoDB interactions in tests."""
    
    def __init__(self, connection_string: str, database_name: str):
        """
        Initialize MongoDB interface.
        
        Args:
            connection_string: MongoDB connection string
            database_name: Database name
        """
        self.connection_string = connection_string
        self.database_name = database_name
        self.client: Optional[pymongo.MongoClient] = None
        self.db: Optional[pymongo.database.Database] = None
    
    def connect(self) -> bool:
        """Connect to MongoDB."""
        try:
            self.client = pymongo.MongoClient(self.connection_string)
            self.db = self.client[self.database_name]
            # Test connection
            self.client.admin.command('ping')
            logger.info(f"Connected to MongoDB: {self.database_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MongoDB."""
        if self.client:
            self.client.close()
            logger.info("Disconnected from MongoDB")
    
    def find_one(self, collection: str, filter: Dict[str, Any]) -> Optional[Dict]:
        """Find a single document in collection."""
        if self.db is None:
            raise ConnectionError("Not connected to MongoDB")
        return self.db[collection].find_one(filter)
    
    def find_many(self, collection: str, filter: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Find multiple documents in collection."""
        if self.db is None:
            raise ConnectionError("Not connected to MongoDB")
        if filter is None:
            filter = {}
        return list(self.db[collection].find(filter))
    
    def count_documents(self, collection: str, filter: Optional[Dict[str, Any]] = None) -> int:
        """Count documents in collection."""
        if self.db is None:
            raise ConnectionError("Not connected to MongoDB")
        if filter is None:
            filter = {}
        return self.db[collection].count_documents(filter)
    
    def insert_one(self, collection: str, document: Dict[str, Any]) -> Any:
        """Insert a document into collection."""
        if self.db is None:
            raise ConnectionError("Not connected to MongoDB")
        return self.db[collection].insert_one(document)
    
    def delete_many(self, collection: str, filter: Dict[str, Any]) -> int:
        """Delete documents from collection."""
        if self.db is None:
            raise ConnectionError("Not connected to MongoDB")
        return self.db[collection].delete_many(filter).deleted_count


class ArangoInterface:
    """Interface for ArangoDB interactions in tests."""
    
    def __init__(self, url: str, username: str, password: str, database_name: str):
        """
        Initialize ArangoDB interface.
        
        Args:
            url: ArangoDB URL
            username: ArangoDB username
            password: ArangoDB password
            database_name: Database name
        """
        self.url = url
        self.username = username
        self.password = password
        self.database_name = database_name
        self.client: Optional[ArangoClient] = None
        self.db: Optional[Any] = None
    
    def connect(self) -> bool:
        """Connect to ArangoDB."""
        try:
            self.client = ArangoClient(hosts=self.url)
            
            # Connect to system database first
            sys_db = self.client.db('_system', username=self.username, password=self.password)
            
            # Check if our database exists and create if it doesn't
            try:
                all_databases = sys_db.databases()
                # Handle both list of dicts and list of objects
                if all_databases and len(all_databases) > 0:
                    database_names = []
                    for db in all_databases:
                        if isinstance(db, dict):
                            database_names.append(db.get('name', db.get('_name', '')))
                        else:
                            # It's an object with a name attribute
                            database_names.append(getattr(db, 'name', getattr(db, '_name', '')))
                    
                    if self.database_name not in database_names:
                        sys_db.create_database(self.database_name)
                else:
                    # No databases exist, create ours
                    sys_db.create_database(self.database_name)
            except Exception as create_error:
                # If we can't check, try to create directly
                try:
                    sys_db.create_database(self.database_name)
                except Exception:
                    # Database might already exist, continue
                    pass
            
            # Connect to our database
            self.db = self.client.db(self.database_name, username=self.username, password=self.password)
            logger.info(f"Connected to ArangoDB: {self.database_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to ArangoDB: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from ArangoDB."""
        if self.client:
            # ArangoDB client doesn't have explicit close method
            logger.info("Disconnected from ArangoDB")
    
    def get_vertex(self, collection: str, key: str) -> Optional[Dict]:
        """Get a vertex from collection."""
        if self.db is None:
            raise ConnectionError("Not connected to ArangoDB")
        return self.db.collection(collection).get(key)
    
    def has_vertex(self, collection: str, key: str) -> bool:
        """Check if a vertex exists in collection."""
        if self.db is None:
            raise ConnectionError("Not connected to ArangoDB")
        return self.db.collection(collection).has(key)
    
    def delete_vertex(self, collection: str, key: str) -> bool:
        """Delete a vertex from collection."""
        if self.db is None:
            raise ConnectionError("Not connected to ArangoDB")
        try:
            self.db.collection(collection).delete(key)
            return True
        except Exception as e:
            logger.warning(f"Failed to delete vertex {key} from {collection}: {e}")
            return False
    
    def delete_all_vertices(self, collection: str) -> int:
        """Delete all vertices from collection."""
        if self.db is None:
            raise ConnectionError("Not connected to ArangoDB")
        try:
            # Use AQL to delete all documents
            aql_query = f"FOR doc IN {collection} REMOVE doc IN {collection}"
            cursor = self.db.aql.execute(aql_query)
            return cursor.count() if hasattr(cursor, 'count') else 0
        except Exception as e:
            logger.warning(f"Failed to delete all vertices from {collection}: {e}")
            return 0
    
    def query(self, query: str, bind_vars: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Execute AQL query."""
        if self.db is None:
            raise ConnectionError("Not connected to ArangoDB")
        if bind_vars is None:
            bind_vars = {}
        cursor = self.db.aql.execute(query, bind_vars=bind_vars)
        return list(cursor)


class QdrantInterface:
    """Interface for Qdrant interactions in tests."""
    
    def __init__(self, host: str, port: int, api_key: Optional[str] = None):
        """
        Initialize Qdrant interface.
        
        Args:
            host: Qdrant host
            port: Qdrant port
            api_key: API key (optional)
        """
        self.host = host
        self.port = port
        self.api_key = api_key
        self.client: Optional[QdrantClient] = None
    
    def connect(self) -> bool:
        """Connect to Qdrant."""
        try:
            if self.api_key:
                self.client = QdrantClient(
                    host=self.host,
                    port=self.port,
                    api_key=self.api_key,
                    prefer_grpc=True
                )
            else:
                self.client = QdrantClient(
                    host=self.host,
                    port=self.port,
                    prefer_grpc=True
                )
            # Test connection
            self.client.get_collections()
            logger.info(f"Connected to Qdrant: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from Qdrant."""
        if self.client:
            # Qdrant client doesn't have explicit close method
            logger.info("Disconnected from Qdrant")
    
    def get_collection_info(self, collection_name: str) -> Optional[Dict]:
        """Get collection information."""
        if not self.client:
            raise ConnectionError("Not connected to Qdrant")
        try:
            return self.client.get_collection(collection_name)
        except Exception:
            return None
    
    def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists."""
        if not self.client:
            raise ConnectionError("Not connected to Qdrant")
        collections = self.client.get_collections()
        return any(col.name == collection_name for col in collections)


class RedisInterface:
    """Interface for Redis interactions in tests."""
    
    def __init__(self, host: str, port: int, db: int = 0, password: Optional[str] = None):
        """
        Initialize Redis interface.
        
        Args:
            host: Redis host
            port: Redis port
            db: Database number
            password: Password (optional)
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.client: Optional[Redis] = None
    
    def connect(self) -> bool:
        """Connect to Redis."""
        try:
            self.client = Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True
            )
            # Test connection
            self.client.ping()
            logger.info(f"Connected to Redis: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from Redis."""
        if self.client:
            self.client.close()
            logger.info("Disconnected from Redis")
    
    def get(self, key: str) -> Optional[str]:
        """Get a key's value."""
        if not self.client:
            raise ConnectionError("Not connected to Redis")
        value = self.client.get(key)
        # Try to parse as JSON if possible
        if value:
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value
    
    def set(self, key: str, value: Any, ex: Optional[int] = None):
        """Set a key's value."""
        if not self.client:
            raise ConnectionError("Not connected to Redis")
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return self.client.set(key, value, ex=ex)
    
    def delete(self, key: str) -> int:
        """Delete a key."""
        if not self.client:
            raise ConnectionError("Not connected to Redis")
        return self.client.delete(key)
    
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if not self.client:
            raise ConnectionError("Not connected to Redis")
        return bool(self.client.exists(key))


# Fixture factories
def create_mongo_interface() -> MongoInterface:
    """Create and connect to MongoDB interface."""
    settings = get_settings()
    mongo = MongoInterface(
        connection_string=f"mongodb://admin:password@localhost:27017/",
        database_name="es"  # Backend uses 'es' as the database name
    )
    mongo.connect()
    return mongo


def create_arango_interface() -> ArangoInterface:
    """Create and connect to ArangoDB interface."""
    settings = get_settings()
    arango = ArangoInterface(
        url="http://localhost:8529",
        username="root",
        password="your_password",
        database_name="es"
    )
    arango.connect()
    return arango
def create_qdrant_interface() -> QdrantInterface:
    """Create and connect to Qdrant interface."""
    qdrant = QdrantInterface(
        host="localhost",
        port=6333,
        api_key=None
    )
    qdrant.connect()
    return qdrant


def create_redis_interface() -> RedisInterface:
    """Create and connect to Redis interface."""
    settings = get_settings()
    redis = RedisInterface(
        host="localhost",
        port=6379,
        db=0,
        password=None
    )
    redis.connect()
    return redis



