import asyncio
import os

from dotenv import load_dotenv

from app.sources.client.sqs.amazon_sqs import AmazonSQSClient
from app.sources.external.amazon_sqs.amazon_sqs_data_source import AmazonSQSDataSource

load_dotenv()

ACCESS_KEY = os.getenv("ACCESS_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
REGION_NAME = os.getenv("REGION_NAME")


async def main() -> None:

    sqs_client = AmazonSQSClient(
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region_name=REGION_NAME,
    )

    data_source = AmazonSQSDataSource(sqs_client)

    #0List queues
    print("📋 Listing queues...")
    list_resp = await data_source.list_queues()
    print(list_resp.to_json())

    #Get queue URL by name
    print("\n🔗 Getting queue URL...")
    queue_name = "MyQueue"
    get_url_resp = await data_source.get_queue_url(queue_name)
    print(get_url_resp.to_json())

    #Send a message
    print("\n📨 Sending message...")
    if get_url_resp.success and get_url_resp.data and "QueueUrl" in get_url_resp.data:
        queue_url = get_url_resp.data["QueueUrl"]
        send_resp = await data_source.send_message(
            queue_url=queue_url,
            message_body="Hello from PipesHub AmazonSQSDataSource example!",
        )
        print(send_resp.to_json())
    else:
        print("⚠️ Unable to retrieve Queue URL. Skipping send_message test.")

    #Receive messages
    print("\n📥 Receciving messages...")
    if get_url_resp.success and get_url_resp.data and "QueueUrl" in get_url_resp.data:
        recv_resp = await data_source.receive_message(
            queue_url=get_url_resp.data["QueueUrl"],
            max_messages=1,
            wait_time=2,
        )
        print(recv_resp.to_json())
    else:
        print("⚠️ Unable to retrieve Queue URL. Skipping receive_message test.")


if __name__ == "__main__":
    asyncio.run(main())
