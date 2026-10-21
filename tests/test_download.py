"""公共数据库下载流程（使用假 HTTP 响应，不访问网络）。"""

from __future__ import annotations

import threading

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.core.models import DownloadResult
from dockflowlab.errors import DownloadCancelled, DownloadError
from dockflowlab.services.download import DownloadClient, download_file


class FakeResponse:
    def __init__(self, *, content: bytes = b"", payload=None):
        self.content = content
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


SDF = b"""2244\n  test\n\n  3  2  0  0  0  0            999 V2000\n    0.0    0.0    0.0 C   0  0\n$$$$\n"""


def test_pubchem_by_cid(monkeypatch):
    client = DownloadClient(AppSettings())

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        if url.endswith("/cids/JSON"):
            return FakeResponse(payload={"IdentifierList": {"CID": [2244]}})
        if "/property/" in url:
            return FakeResponse(
                payload={
                    "PropertyTable": {
                        "Properties": [
                            {
                                "Title": "Aspirin",
                                "IUPACName": "2-acetoxybenzoic acid",
                                "MolecularFormula": "C9H8O4",
                                "MolecularWeight": 180.16,
                                "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                            }
                        ]
                    }
                }
            )
        if "/record/SDF" in url:
            assert params["record_type"] == "3d"
            return FakeResponse(content=SDF)
        raise AssertionError(url)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.pubchem("2244")
    assert result.data == SDF
    assert result.display_name == "Aspirin"
    assert result.filename == "pubchem_2244.sdf"
    assert result.meta["cid"] == 2244
    assert result.meta["record_type"] == "3d"
    assert result.meta["warnings"] == []


def test_pubchem_falls_back_to_2d(monkeypatch):
    client = DownloadClient(AppSettings())
    calls = []

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        if url.endswith("/cids/JSON"):
            return FakeResponse(payload={"IdentifierList": {"CID": [1]}})
        if "/property/" in url:
            return FakeResponse(payload={"PropertyTable": {"Properties": [{"Title": "X"}]}})
        calls.append(params["record_type"])
        if params["record_type"] == "3d":
            raise DownloadError("no 3d")
        return FakeResponse(content=SDF)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.pubchem("1")
    assert calls == ["3d", "2d"]
    assert result.meta["record_type"] == "2d"
    assert result.meta["warnings"]


def test_pubchem_rejects_non_sdf(monkeypatch):
    client = DownloadClient(AppSettings())
    monkeypatch.setattr(
        client,
        "_get",
        lambda *a, **k: FakeResponse(content=b"<html>error</html>"),
    )
    # CID 查询会先走 cids 接口；这里直接给一个 CID，跳过名称解析
    with pytest.raises(DownloadError, match="SDF"):
        client.pubchem("2244")


def test_pdb_falls_back_to_cif(monkeypatch):
    client = DownloadClient(AppSettings())

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        if url.endswith("1ABC.pdb"):
            raise DownloadError("404")
        if url.endswith("1ABC.cif"):
            return FakeResponse(content=b"data_1ABC\n_atom_site.group_PDB ATOM\n")
        if "data.rcsb.org" in url:
            return FakeResponse(payload={"struct": {"title": "Test protein"}, "rcsb_entry_info": {"resolution_combined": [2.1]}})
        raise AssertionError(url)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.pdb("1abc")
    assert result.filename == "pdb_1ABC.cif"
    assert result.meta["format"] == "cif"
    assert result.meta["title"] == "Test protein"
    assert result.meta["warnings"]


def test_pdb_rejects_bad_id():
    with pytest.raises(DownloadError, match="4 位"):
        DownloadClient().pdb("abc")


def test_alphafold_uses_api_url(monkeypatch):
    client = DownloadClient(AppSettings())
    pdb = b"ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        if "api/prediction" in url:
            return FakeResponse(
                payload=[
                    {
                        "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v4.pdb",
                        "entryId": "AF-P00533-F1",
                        "uniprotStart": 1,
                        "uniprotEnd": 100,
                    }
                ]
            )
        if url.endswith(".pdb"):
            return FakeResponse(content=pdb)
        raise AssertionError(url)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.alphafold("p00533")
    assert result.data == pdb
    assert result.meta["uniprot"] == "P00533"
    assert "预测模型" in result.meta["warnings"][0]


def test_chembl_search_and_download(monkeypatch):
    client = DownloadClient(AppSettings())

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        if url.endswith("/molecule/search"):
            return FakeResponse(payload={"molecules": [{"molecule_chembl_id": "CHEMBL25", "pref_name": "ASPIRIN"}]})
        if "/molecule/CHEMBL25" in url:
            return FakeResponse(content=SDF.replace(b"$$$$\n", b""))
        raise AssertionError(url)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.chembl("aspirin")
    assert result.filename == "chembl_CHEMBL25.sdf"
    assert result.display_name == "ASPIRIN"
    assert result.data.endswith(b"$$$$\n")


def test_cancelled_download_stops_before_request(monkeypatch):
    """取消后不再发起新的网络请求，并抛出可识别的取消异常。"""
    client = DownloadClient(AppSettings())
    cancel = threading.Event()
    cancel.set()
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args)
        raise AssertionError("取消后不应再发起请求")

    monkeypatch.setattr(client, "_get", fake_get)
    with pytest.raises(DownloadCancelled):
        client.pubchem("aspirin", cancel=cancel)
    assert calls == []


def test_cancel_between_download_stages(monkeypatch):
    """第一个请求返回后如果已经取消，就不再继续后续请求。"""
    client = DownloadClient(AppSettings())
    cancel = threading.Event()
    calls = []

    def fake_get(url, *, params=None, accept="*/*", timeout=None):
        calls.append(url)
        if url.endswith("/cids/JSON"):
            # 模拟用户在解析 CID 期间点击取消
            cancel.set()
            return FakeResponse(payload={"IdentifierList": {"CID": [2244]}})
        raise AssertionError("取消后不应继续下载")

    monkeypatch.setattr(client, "_get", fake_get)
    with pytest.raises(DownloadCancelled):
        client.pubchem("aspirin", cancel=cancel)
    assert len(calls) == 1


def test_download_file_writes(monkeypatch, tmp_path):
    client = DownloadClient(AppSettings())
    monkeypatch.setattr(
        DownloadClient,
        "pdb",
        lambda self, query, **kwargs: DownloadResult(
            data=b"ATOM\n", filename="x.pdb", display_name="X", meta={}
        ),
    )
    destination = tmp_path / "out"
    destination.mkdir()
    path, result = download_file("pdb", "1ABC", destination)
    assert path == destination / "x.pdb"
    assert path.read_bytes() == b"ATOM\n"
    assert result.display_name == "X"
