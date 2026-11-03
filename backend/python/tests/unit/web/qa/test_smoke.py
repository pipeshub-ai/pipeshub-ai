from tests.unit.web.qa.lab import WebLab, results


async def test_search_read_and_cite_end_to_end(lab: WebLab) -> None:
    lab.engines.set_ddgs(results(("http://docs.test/guide", "The Guide", "How to configure it")))
    lab.site.html("http://docs.test/guide", "The Guide", text="Configuration is done in settings. " * 40)
    agent = lab.agent()

    searched = await agent.search("how to configure")
    assert "site: docs.test" in searched.text
    assert searched.view["rows"][0]["url"] == "http://docs.test/guide"

    read = await agent.fetch(agent.ref_for("http://docs.test/guide"))
    assert "Configuration is done in settings." in read.text
    assert read.view["fields"][0]["value"] == "The Guide"

    answer, citations = agent.cite(f"Use settings [source]({agent.ref_for('http://docs.test/guide')}).")
    assert "docs.test/guide" in answer
    assert citations[0]["metadata"]["recordName"] == "The Guide"
