from abc import ABC, abstractmethod
from typing import Any, Dict, List


class IDataProcessor(ABC):
    """Base interface for data processing"""

    @abstractmethod
    async def add_record(self, record_data: Dict[str, Any]) -> bool:
        """Add a record to the data service"""
        pass

    @abstractmethod
    async def update_record(self, record_data: Dict[str, Any]) -> bool:
        """Update a record in the data service"""
        pass

    @abstractmethod
    async def delete_record(self, record_id: str) -> bool:
        """Delete a record from the data service"""
        pass

    @abstractmethod
    async def get_record(self, record_id: str) -> Dict[str, Any]:
        """Get a record from the data service"""
        pass

    @abstractmethod
    async def get_records(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get records from the data service"""
        pass

    @abstractmethod
    async def add_user(self, user_data: Dict[str, Any]) -> bool:
        """Add a user to the data service"""
        pass

    @abstractmethod
    async def update_user(self, user_data: Dict[str, Any]) -> bool:
        """Update a user in the data service"""
        pass

    @abstractmethod
    async def delete_user(self, user_id: str) -> bool:
        """Delete a user from the data service"""
        pass

    @abstractmethod
    async def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get a user from the data service"""
        pass

    @abstractmethod
    async def add_user_group(self, user_group_data: Dict[str, Any]) -> bool:
        """Add a user group to the data service"""
        pass

    @abstractmethod
    async def update_user_group(self, user_group_data: Dict[str, Any]) -> bool:
        """Update a user group in the data service"""
        pass

    @abstractmethod
    async def delete_user_group(self, user_group_id: str) -> bool:
        """Delete a user group from the data service"""
        pass

    @abstractmethod
    async def get_user_group(self, user_group_id: str) -> Dict[str, Any]:
        """Get a user group from the data service"""
        pass

    @abstractmethod
    async def add_record_group(self, record_group_data: Dict[str, Any]) -> bool:
        """Add a record group to the data service"""
        pass

    @abstractmethod
    async def update_record_group(self, record_group_data: Dict[str, Any]) -> bool:
        """Update a record group in the data service"""
        pass

    @abstractmethod
    async def delete_record_group(self, record_group_id: str) -> bool:
        """Delete a record group from the data service"""
        pass

    @abstractmethod
    async def get_record_group(self, record_group_id: str) -> Dict[str, Any]:
        """Get a record group from the data service"""
        pass
