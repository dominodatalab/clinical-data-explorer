from mcp_server.services import data_loading


def test_find_data_files_only_lists_repo_datasets_folder(monkeypatch, tmp_path):
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


def test_load_dataset_resolves_bare_name_from_datasets_folder(monkeypatch, tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "local_fixture.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)

    df = data_loading.load_dataset("local_fixture.csv")

    assert df.to_dict(orient="records") == [{"id": 1, "value": "alpha"}]


def test_extract_dataset_metadata_resolves_bare_name_from_datasets_folder(monkeypatch, tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    fixture = datasets_dir / "ae.json"
    fixture.write_text(
        '{"datasetJSONVersion":"1.1.0","name":"AE","label":"Adverse Events","records":1,'
        '"columns":[{"name":"AESEQ","label":"Sequence Number","dataType":"integer","length":8}],'
        '"rows":[[1]]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(data_loading, "datasets_folder", datasets_dir)

    metadata = data_loading.extract_dataset_metadata(data_loading.Path("ae.json"))

    assert metadata["available"] is True
    assert metadata["format"].startswith("CDISC Dataset-JSON")
    assert metadata["variables"]["rows"][0][0] == "AESEQ"
