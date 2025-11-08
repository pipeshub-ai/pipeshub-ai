from app.sources.client.bamboohr.bamboohr import BambooHRClient
from app.sources.external.bamboohr.bamboohr import BambooHRDataSource


def main() -> None:
    # NOTE: Replace values with real ones only when testing with a real account
    client = BambooHRClient(subdomain="your_subdomain", api_key="your_api_key")
    ds = BambooHRDataSource(client)

    print(ds.list_employees())
    print(ds.get_employee("123"))
    print(ds.get_time_off_balance())


if __name__ == "__main__":
    main()
