from mcp_server.services import data_loading


def test_find_data_files_only_lists_repo_datasets_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("DOMINO_RUN_ID", raising=False)
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "local.csv").write_text("id\n1\n", encoding="utf-8")
    (datasets_dir / "local.parquet").write_bytes(b"not used by discovery")
    (datasets_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    nested_dir = datasets_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "nested.csv").write_text("id\n2\n", encoding="utf-8")

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)

    assert sorted(f["name"] for f in data_loading.find_data_files()) == [
        "local.csv",
        "local.parquet",
    ]


def test_find_data_files_hides_repo_datasets_in_domino(monkeypatch, tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "local.csv").write_text("id\n1\n", encoding="utf-8")

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)
    monkeypatch.setenv("DOMINO_RUN_ID", "run-123")

    assert data_loading.find_data_files() == []


def test_load_dataset_resolves_bare_name_from_datasets_folder(monkeypatch, tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "local_fixture.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)

    df = data_loading.load_dataset("local_fixture.csv")

    assert df.to_dict(orient="records") == [{"id": 1, "value": "alpha"}]
