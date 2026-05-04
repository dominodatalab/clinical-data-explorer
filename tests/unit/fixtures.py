"""Test helpers for backend unit tests."""

import sys
import types


def install_fake_dataset_client(monkeypatch, dataset_files_by_key):
    captured = {"dataset_keys": []}

    class FakeDataset:
        def __init__(self, files):
            self._files = files

        def list_files(self):
            return [types.SimpleNamespace(name=name) for name in self._files]

    class FakeDatasetClient:
        def __init__(self, token):
            captured["token"] = token

        def get_dataset(self, dataset_key):
            captured["dataset_keys"].append(dataset_key)
            return FakeDataset(dataset_files_by_key[dataset_key])

    domino_data_module = types.ModuleType("domino_data")
    domino_data_datasets_module = types.ModuleType("domino_data.datasets")
    domino_data_datasets_module.DatasetClient = FakeDatasetClient
    domino_data_module.datasets = domino_data_datasets_module
    monkeypatch.setitem(sys.modules, "domino_data", domino_data_module)
    monkeypatch.setitem(sys.modules, "domino_data.datasets", domino_data_datasets_module)
    return captured


def install_fake_netapp_client(monkeypatch, volume_files_by_key, file_contents_by_name, volume_id="nv-1"):
    captured = {
        "tokens": [],
        "get_volume_calls": [],
        "list_files_calls": [],
        "updated_snapshot_versions": [],
        "downloaded_files": [],
    }

    class FakeVolumeFile:
        def __init__(self, file_name):
            self.file_name = file_name

        def download_fileobj(self, fileobj):
            captured["downloaded_files"].append(self.file_name)
            fileobj.write(file_contents_by_name[self.file_name])

    class FakeVolume:
        def __init__(self, volume_key):
            self.volume_key = volume_key
            self.volume_id = volume_id

        def update(self, config):
            captured["updated_snapshot_versions"].append(config.snapshot_version)

        def list_files(self):
            return [types.SimpleNamespace(key=name) for name in volume_files_by_key[self.volume_key]]

        def File(self, file_name):
            return FakeVolumeFile(file_name)

    class FakeNetAppVolumeClient:
        snapshots_by_volume = {}

        def __init__(self, token):
            captured["tokens"].append(token)

        def get_volume(self, volume_key):
            captured["get_volume_calls"].append(volume_key)
            return FakeVolume(volume_key)

        def list_files(self, volume_key):
            captured["list_files_calls"].append(volume_key)
            return volume_files_by_key[volume_key]

        def list_snapshots(self, volume_unique_name):
            captured.setdefault("list_snapshots_calls", []).append(volume_unique_name)
            return list(self.snapshots_by_volume.get(volume_unique_name, []))

    class FakeNetAppVolumeConfig:
        def __init__(self, snapshot_version):
            self.snapshot_version = snapshot_version

    domino_data_module = types.ModuleType("domino_data")
    domino_data_netapp_module = types.ModuleType("domino_data.netapp_volumes")
    domino_data_data_sources_module = types.ModuleType("domino_data.data_sources")
    domino_data_netapp_module.NetAppVolumeClient = FakeNetAppVolumeClient
    domino_data_data_sources_module.NetAppVolumeConfig = FakeNetAppVolumeConfig
    domino_data_module.netapp_volumes = domino_data_netapp_module
    domino_data_module.data_sources = domino_data_data_sources_module
    monkeypatch.setitem(sys.modules, "domino_data", domino_data_module)
    monkeypatch.setitem(sys.modules, "domino_data.netapp_volumes", domino_data_netapp_module)
    monkeypatch.setitem(sys.modules, "domino_data.data_sources", domino_data_data_sources_module)
    return captured
