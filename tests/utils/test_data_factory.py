"""
Test data factory for generating realistic test data.

This module provides factories for generating test data using Faker,
making it easy to create consistent, realistic test data.
"""

import random
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from faker import Faker # type: ignore

fake: Faker = Faker()


class TestDataFactory:
    """Factory for generating test data."""
    
    @staticmethod
    def user(
        username: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test user data.
        
        Args:
            username: Optional username (generated if not provided)
            email: Optional email (generated if not provided)
            password: Optional password (generated if not provided)
            **kwargs: Additional user fields
            
        Returns:
            Dictionary containing user data
            
        Example:
            user = TestDataFactory.user()
            user_with_custom_email = TestDataFactory.user(email="test@example.com")
        """
        return {
            "username": username or fake.user_name(),
            "email": email or fake.email(),
            "password": password or fake.password(length=12),
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "phone": fake.phone_number(),
            "address": fake.address(),
            "date_of_birth": fake.date_of_birth(minimum_age=18, maximum_age=80).isoformat(),
            **kwargs
        }
    
    @staticmethod
    def admin_user(**kwargs) -> Dict[str, Any]:
        """
        Generate admin user data.
        
        Args:
            **kwargs: Additional user fields
            
        Returns:
            Dictionary containing admin user data
        """
        user_data = TestDataFactory.user(**kwargs)
        user_data["role"] = "admin"
        user_data["is_admin"] = True
        user_data["permissions"] = ["read", "write", "delete", "admin"]
        return user_data
    
    @staticmethod
    def organization(
        name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test organization data.
        
        Args:
            name: Optional organization name
            **kwargs: Additional organization fields
            
        Returns:
            Dictionary containing organization data
        """
        return {
            "name": name or fake.company(),
            "description": fake.catch_phrase(),
            "industry": fake.bs(),
            "website": fake.url(),
            "email": fake.company_email(),
            "phone": fake.phone_number(),
            "address": fake.address(),
            "country": fake.country(),
            "employees": random.randint(10, 10000),
            **kwargs
        }
    
    @staticmethod
    def api_key(
        name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test API key data.
        
        Args:
            name: Optional API key name
            **kwargs: Additional API key fields
            
        Returns:
            Dictionary containing API key data
        """
        return {
            "name": name or f"api_key_{fake.word()}",
            "key": f"pk_{uuid.uuid4().hex}",
            "secret": f"sk_{uuid.uuid4().hex}",
            "permissions": random.sample(["read", "write", "delete", "admin"], k=random.randint(1, 3)),
            "expires_at": (datetime.now() + timedelta(days=365)).isoformat(),
            **kwargs
        }
    
    @staticmethod
    def document(
        title: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test document data.
        
        Args:
            title: Optional document title
            **kwargs: Additional document fields
            
        Returns:
            Dictionary containing document data
        """
        return {
            "title": title or fake.sentence(nb_words=6),
            "content": fake.text(max_nb_chars=1000),
            "author": fake.name(),
            "tags": fake.words(nb=5),
            "status": random.choice(["draft", "published", "archived"]),
            "created_at": fake.date_time_this_year().isoformat(),
            **kwargs
        }
    
    @staticmethod
    def connector_config(
        connector_type: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test connector configuration.
        
        Args:
            connector_type: Optional connector type
            **kwargs: Additional connector fields
            
        Returns:
            Dictionary containing connector configuration
        """
        connector_types: List[str] = ["github", "jira", "confluence", "slack", "notion"]
        selected_type: str = connector_type or random.choice(connector_types)
        
        return {
            "type": selected_type,
            "name": f"{selected_type}_{fake.word()}",
            "enabled": random.choice([True, False]),
            "config": {
                "api_url": fake.url(),
                "api_key": fake.uuid4(),
                "timeout": random.randint(10, 60),
            },
            **kwargs
        }
    
    @staticmethod
    def pipeline(
        name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate test pipeline data.
        
        Args:
            name: Optional pipeline name
            **kwargs: Additional pipeline fields
            
        Returns:
            Dictionary containing pipeline data
        """
        return {
            "name": name or f"pipeline_{fake.word()}",
            "description": fake.sentence(),
            "status": random.choice(["active", "inactive", "paused"]),
            "schedule": random.choice(["@hourly", "@daily", "@weekly", "*/30 * * * *"]),
            "source": random.choice(["github", "jira", "confluence", "slack"]),
            "destination": random.choice(["elasticsearch", "postgres", "s3"]),
            **kwargs
        }
    
    @staticmethod
    def batch(count: int, factory_func, **kwargs) -> List[Dict[str, Any]]:
        """
        Generate multiple test data items.
        
        Args:
            count: Number of items to generate
            factory_func: Factory function to use
            **kwargs: Additional fields for each item
            
        Returns:
            List of generated data items
            
        Example:
            users = TestDataFactory.batch(10, TestDataFactory.user)
            orgs = TestDataFactory.batch(5, TestDataFactory.organization, country="USA")
        """
        return [factory_func(**kwargs) for _ in range(count)]
    
    @staticmethod
    def random_string(length: int = 10, prefix: str = "") -> str:
        """
        Generate random string.
        
        Args:
            length: Length of random string
            prefix: Optional prefix
            
        Returns:
            Random string
        """
        return f"{prefix}{fake.lexify('?' * length)}"
    
    @staticmethod
    def random_email(domain: Optional[str] = None) -> str:
        """
        Generate random email address.
        
        Args:
            domain: Optional domain (generated if not provided)
            
        Returns:
            Random email address
        """
        if domain:
            return f"{fake.user_name()}@{domain}"
        return fake.email()
    
    @staticmethod
    def random_url(scheme: str = "https") -> str:
        """
        Generate random URL.
        
        Args:
            scheme: URL scheme (http or https)
            
        Returns:
            Random URL
        """
        return f"{scheme}://{fake.domain_name()}/{fake.uri_path()}"
    
    @staticmethod
    def random_date(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> str:
        """
        Generate random date.
        
        Args:
            start_date: Start date range
            end_date: End date range
            
        Returns:
            Random date in ISO format
        """
        start_date_val: datetime = start_date if start_date else datetime.now() - timedelta(days=365)
        end_date_val: datetime = end_date if end_date else datetime.now()
        
        return fake.date_time_between(start_date=start_date_val, end_date=end_date_val).isoformat()
    
    @staticmethod
    def random_json_payload(depth: int = 2) -> Dict[str, Any]:
        """
        Generate random JSON payload.
        
        Args:
            depth: Nesting depth
            
        Returns:
            Random JSON-compatible dictionary
        """
        if depth <= 0:
            return {
                fake.word(): random.choice([
                    fake.word(),
                    fake.random_int(0, 1000),
                    fake.boolean(),
                ])
                for _ in range(random.randint(1, 3))
            }
        
        return {
            fake.word(): random.choice([
                fake.word(),
                fake.random_int(0, 1000),
                fake.boolean(),
                TestDataFactory.random_json_payload(depth - 1),
            ])
            for _ in range(random.randint(2, 5))
        }


# Convenience aliases
generate_user: callable = TestDataFactory.user
generate_users: callable = lambda count, **kwargs: TestDataFactory.batch(count, TestDataFactory.user, **kwargs)
generate_organization: callable = TestDataFactory.organization
generate_organizations: callable = lambda count, **kwargs: TestDataFactory.batch(count, TestDataFactory.organization, **kwargs)

