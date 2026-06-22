from mcp_server import app as mcp_app


def test_dataset_discovery_stays_exposed_as_mcp_tool(monkeypatch):
    captured = {}

    class FakeFastApiMCP:
        def __init__(self, app, **kwargs):
            del app
            captured.update(kwargs)

        def mount(self):
            captured["mounted"] = True

    monkeypatch.setattr(mcp_app, "FastApiMCP", FakeFastApiMCP)

    app = mcp_app.create_app()

    assert app.openapi()["paths"]["/datasets/list"]["get"]["operationId"] == "list_available_datasets"
    assert "list_available_datasets" not in captured["exclude_operations"]
    assert captured["mounted"] is True
