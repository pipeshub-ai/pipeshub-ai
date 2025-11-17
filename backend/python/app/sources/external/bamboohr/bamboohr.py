from typing import Any

from app.sources.client.bamboohr.bamboohr import BambooHRClient


class BambooHRDataSource:
    """
    DataSource wrapper around BambooHRClient.

    Thin wrapper that provides business-meaningful methods.
    """

    def __init__(self, client: BambooHRClient) -> None:
        self.client = client

    # minimal endpoint placeholders — we will fill later
    def list_employees(self) -> Any:
        """
        List all employees.

        Returns:
            List of employees.
        """
        return self.client.get("/employees")

    def get_employee(self, employee_id: str) -> Any:
        """
        Get details of a specific employee.

        Args:
            employee_id: ID of the employee.

        Returns:
            Employee details.
        """
        return self.client.get(f"/employees/{employee_id}")
