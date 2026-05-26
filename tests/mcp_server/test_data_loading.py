from mcp_server.services import data_loading


def test_load_dataset_resolves_bare_name_from_datasets_folder(monkeypatch, tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "local_fixture.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)

    df = data_loading.load_dataset("local_fixture.csv")

    assert df.to_dict(orient="records") == [{"id": 1, "value": "alpha"}]
