from app.sources.client.bamboohr.bamboohr import BambooHRClient


class BambooHRDataSource:
    """
    DataSource wrapper around BambooHRClient.

    Thin wrapper that provides business-meaningful methods.
    """

    def __init__(self, client: BambooHRClient) -> None:
        self.client = client

    # minimal endpoint placeholders — we will fill later
    def list_employees(self):
        return self.client.get("/employees/directory")

    def get_employee(self, employee_id: str):
        return self.client.get(f"/employees/{employee_id}")
