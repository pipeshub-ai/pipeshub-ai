# ruff: noqa
import asyncio
import json
import os
from typing import Any

from app.sources.client.servicenow.servicenow import (
    ServiceNowClient,
    ServiceNowUsernamePasswordConfig,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE_URL")
USERNAME = os.getenv("SERVICENOW_USERNAME")
PASSWORD = os.getenv("SERVICENOW_PASSWORD")


def print_response(title, response):
    print(f"\n{'='*80}\n{title}\n{'='*80}")
    if response.success and response.data:
        print(json.dumps(response.data, indent=2, default=str))
    else:
        print(f"Error: {response.error}")
        if response.message:
            print(f"Message: {response.message}")


async def test_users(ds):
    print("\nTESTING USERS")
    r = await ds.get_now_table_tableName(
        tableName="sys_user",
        sysparm_limit="5",
        sysparm_fields="sys_id,user_name,first_name,last_name,email",
        sysparm_display_value="true",
    )
    print_response("Users", r)


async def test_user_groups(ds):
    print("\nTESTING USER GROUPS")
    r = await ds.get_now_table_tableName(
        tableName="sys_user",
        sysparm_query=f"user_name={USERNAME}",
        sysparm_fields="sys_id",
        sysparm_display_value="true",
    )
    if r.success and r.data and "result" in r.data and r.data["result"]:
        user_id = r.data["result"][0].get("sys_id")
        r = await ds.get_now_table_tableName(
            tableName="sys_user_grmember",
            sysparm_query=f"user={user_id}",
            sysparm_fields="group.name,group.sys_id",
            sysparm_display_value="true",
        )
        print_response(f"Groups for {USERNAME}", r)


async def test_group_members(ds):
    print("\nTESTING GROUP MEMBERS")
    r = await ds.get_now_table_tableName(
        tableName="sys_user_group",
        sysparm_query="active=true",
        sysparm_limit="1",
        sysparm_fields="sys_id,name",
        sysparm_display_value="true",
    )
    if r.success and r.data and "result" in r.data and r.data["result"]:
        group_id = r.data["result"][0].get("sys_id")
        name = r.data["result"][0].get("name")
        r = await ds.get_now_table_tableName(
            tableName="sys_user_grmember",
            sysparm_query=f"group={group_id}",
            sysparm_fields="user.name,user.email",
            sysparm_display_value="true",
            sysparm_limit="10",
        )
        print_response(f"Members of {name}", r)


async def test_knowledge_bases(ds):
    print("\nTESTING KNOWLEDGE BASES")
    r = await ds.get_now_table_tableName(
        tableName="kb_knowledge_base",
        sysparm_limit="10",
        sysparm_fields="sys_id,title,owner,active",
        sysparm_display_value="true",
    )
    print_response("Knowledge Bases", r)


async def test_accessible_kbs(ds):
    print("\nTESTING ACCESSIBLE KBs")
    r = await ds.get_now_table_tableName(
        tableName="sys_user",
        sysparm_query=f"user_name={USERNAME}",
        sysparm_fields="sys_id",
        sysparm_display_value="true",
    )
    if r.success and r.data and "result" in r.data and r.data["result"]:
        user_id = r.data["result"][0].get("sys_id")

        # 1. Get owned KBs
        r = await ds.get_now_table_tableName(
            tableName="kb_knowledge_base",
            sysparm_query=f"owner={user_id}",
            sysparm_fields="sys_id,title",
            sysparm_display_value="true",
        )
        print_response("Owned KBs", r)

        # 2. Get KBs where user is a manager (editable)
        r = await ds.get_now_table_tableName(
            tableName="kb_knowledge_base",
            sysparm_query=f"kb_managersLIKE{user_id}",
            sysparm_fields="sys_id,title,kb_managers",
            sysparm_display_value="true",
            sysparm_limit="10",
        )
        print_response("Manageable KBs (has edit rights)", r)

        # 3. Get readable KBs via permission table
        r = await ds.get_now_table_tableName(
            tableName="kb_uc_can_read_mtom",
            sysparm_fields="kb_knowledge_base,kb_knowledge_base.title,user_criteria",
            sysparm_display_value="true",
            sysparm_limit="10",
        )
        print_response("Readable KBs (sample)", r)


async def test_knowledge_articles(ds):
    print("\nTESTING ARTICLES")
    r = await ds.get_now_table_tableName(
        tableName="kb_knowledge",
        sysparm_query="workflow_state=published",
        sysparm_limit="5",
        sysparm_fields="sys_id,number,short_description,author",
        sysparm_display_value="true",
    )
    print_response("Published Articles", r)


async def test_authored_articles(ds):
    print("\nTESTING AUTHORED ARTICLES")
    r = await ds.get_now_table_tableName(
        tableName="sys_user",
        sysparm_query=f"user_name={USERNAME}",
        sysparm_fields="sys_id",
        sysparm_display_value="true",
    )
    if r.success and r.data and "result" in r.data and r.data["result"]:
        user_id = r.data["result"][0].get("sys_id")
        r = await ds.get_now_table_tableName(
            tableName="kb_knowledge",
            sysparm_query=f"author={user_id}",
            sysparm_fields="sys_id,number,short_description",
            sysparm_display_value="true",
            sysparm_limit="10",
        )
        print_response(f"Articles by {USERNAME}", r)


async def test_article_permissions(ds):
    print("\nTESTING ARTICLE PERMISSIONS")
    r = await ds.get_now_table_tableName(
        tableName="kb_knowledge",
        sysparm_query="workflow_state=published",
        sysparm_limit="1",
        sysparm_fields="sys_id,number,can_read_user_criteria,can_contribute_user_criteria",
        sysparm_display_value="true",
    )
    if r.success and r.data and "result" in r.data and r.data["result"]:
        article = r.data["result"][0]
        print(
            f"Article {article.get('number')}: Read={article.get('can_read_user_criteria')}, Write={article.get('can_contribute_user_criteria')}"
        )


async def main():
    if not all([INSTANCE_URL, USERNAME, PASSWORD]):
        print(
            "Error: Set SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD"
        )
        return
    print(f"Testing ServiceNow API\nInstance: {INSTANCE_URL}\nUser: {USERNAME}")
    config = ServiceNowUsernamePasswordConfig(
        instance_url=INSTANCE_URL, username=USERNAME, password=PASSWORD
    )
    client = ServiceNowClient.build_with_config(config)
    ds = ServiceNowDataSource(client)
    try:
        await test_users(ds)
        await test_user_groups(ds)
        await test_group_members(ds)
        await test_knowledge_bases(ds)
        await test_accessible_kbs(ds)
        await test_knowledge_articles(ds)
        await test_authored_articles(ds)
        await test_article_permissions(ds)
        print("\nAll tests completed!")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
