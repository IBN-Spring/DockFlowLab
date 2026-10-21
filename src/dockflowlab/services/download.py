"""从公共数据库获取结构文件。

支持：
* PubChem：按名称、CID 或 SMILES 获取小分子（优先 3D SDF）；
* RCSB PDB：按 4 位 PDB ID 获取实验结构（PDB 不可用时回退 mmCIF）；
* AlphaFold DB：按 UniProt 登录号获取预测结构；
* ChEMBL：按 ChEMBL ID 或名称获取小分子。

所有下载都经过同一个 Session（自动带代理、重试与超时），并在写入工作目录前
做最小内容校验，避免把错误页面当成结构文件。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.parse import quote

from ..config import AppSettings
from ..core.models import DownloadResult
from ..errors import DownloadCancelled, DownloadError
from ..logging_setup import get_logger
from ..version import __version__

log = get_logger("download")

USER_AGENT = f"DockFlowLab/{__version__} (desktop docking application)"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
RCSB_FILES = "https://files.rcsb.org/download"
RCSB_DATA = "https://data.rcsb.org/rest/v1/core/entry"
ALPHAFOLD_API = "https://alphafold.ebi.ac.uk/api/prediction"
ALPHAFOLD_FILES = "https://alphafold.ebi.ac.uk/files"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"

_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_UNIPROT = re.compile(r"^[A-Z0-9]{6,10}$")
_CHEMBL_ID = re.compile(r"^CHEMBL\d+$", re.IGNORECASE)
_CID = re.compile(r"^\d+$")


def _clean_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _raise_if_cancelled(cancel: threading.Event | None) -> None:
    """在每次网络请求前检查取消；已发出的请求等它自行返回。"""
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled("已取消下载。")


def _safe_filename(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return cleaned or fallback


class DownloadClient:
    """带缓存会话的下载客户端。"""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or AppSettings()
        self._session = None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    @property
    def session(self):
        if self._session is None:
            try:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
            except ImportError as exc:  # pragma: no cover - 打包环境总会包含
                raise DownloadError("缺少网络组件 requests，无法在线下载。") from exc

            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
            proxies = self.settings.proxies()
            if proxies:
                session.proxies.update(proxies)
            retry = Retry(
                total=3,
                connect=3,
                read=2,
                backoff_factor=0.8,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET",),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session
        return self._session

    def _get(self, url: str, *, params: dict | None = None, accept: str = "*/*", timeout: int | None = None):
        import requests

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=timeout or self.settings.request_timeout,
                headers={"Accept": accept},
            )
        except requests.RequestException as exc:
            raise DownloadError(self._network_hint(str(exc))) from exc
        if response.status_code == 404:
            raise DownloadError(f"服务器上没有找到该条目（404）：{url}")
        if response.status_code >= 400:
            raise DownloadError(
                f"下载失败（HTTP {response.status_code}）。{self._proxy_hint()}"
            )
        return response

    def _network_hint(self, detail: str) -> str:
        hint = "无法连接下载服务器，请检查网络。"
        if self.settings.proxy_url():
            hint += f" 当前代理：{self.settings.proxy_url()}，可尝试在设置中修改或留空。"
        else:
            hint += " 如果所在网络需要代理，请在设置中填写代理地址（例如 127.0.0.1:7897）。"
        return f"{hint}\n技术信息：{detail}"

    def _proxy_hint(self) -> str:
        return "如网络受限，请在设置中配置代理后重试。" if not self.settings.proxy_url() else ""

    # ------------------------------------------------------------------
    # PubChem
    # ------------------------------------------------------------------
    def pubchem(
        self, query: str, *, cancel: threading.Event | None = None
    ) -> DownloadResult:
        _raise_if_cancelled(cancel)
        query = _clean_text(query)
        if not query:
            raise DownloadError("请输入化合物名称、PubChem CID 或 SMILES。")

        cid = self._pubchem_cid(query)
        _raise_if_cancelled(cancel)
        properties = self._pubchem_properties(cid)
        _raise_if_cancelled(cancel)
        record_type = "3d"
        try:
            response = self._get(
                f"{PUBCHEM}/compound/cid/{cid}/record/SDF",
                params={"record_type": "3d"},
            )
        except DownloadCancelled:
            raise
        except DownloadError:
            _raise_if_cancelled(cancel)
            record_type = "2d"
            response = self._get(
                f"{PUBCHEM}/compound/cid/{cid}/record/SDF",
                params={"record_type": "2d"},
            )
        data = response.content
        if b"$$$$" not in data or b"V2000" not in data and b"V3000" not in data:
            raise DownloadError("PubChem 返回的内容不是有效的 SDF 文件。")

        title = _clean_text(properties.get("Title"))
        iupac = _clean_text(properties.get("IUPACName"))
        display = title or iupac or query
        meta = {
            "source": "PubChem",
            "cid": cid,
            "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
            "formula": _clean_text(properties.get("MolecularFormula")),
            "molecular_weight": properties.get("MolecularWeight"),
            "smiles": _clean_text(properties.get("CanonicalSMILES")),
            "record_type": record_type,
            "warnings": [] if record_type == "3d" else ["该化合物没有 3D 构象，已下载 2D 结构，导入后会自动生成 3D 构象。"],
        }
        return DownloadResult(
            data=data,
            filename=f"pubchem_{cid}.sdf",
            display_name=display,
            meta=meta,
        )

    def _pubchem_cid(self, query: str) -> int:
        if _CID.match(query):
            return int(query)
        namespace = "smiles" if self._looks_like_smiles(query) else "name"
        encoded = quote(query, safe="")
        try:
            response = self._get(f"{PUBCHEM}/compound/{namespace}/{encoded}/cids/JSON")
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            if namespace == "smiles":
                raise DownloadError(f"PubChem 无法识别该 SMILES：{query}") from exc
            raise DownloadError(f"PubChem 没有找到名为“{query}”的化合物。") from exc
        try:
            payload = response.json()
            cids = payload["IdentifierList"]["CID"]
            return int(cids[0])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DownloadError(f"PubChem 没有找到与“{query}”匹配的化合物。") from exc

    def _pubchem_properties(self, cid: int) -> dict:
        url = (
            f"{PUBCHEM}/compound/cid/{cid}/property/"
            "Title,IUPACName,MolecularFormula,MolecularWeight,CanonicalSMILES/JSON"
        )
        try:
            response = self._get(url)
            return response.json()["PropertyTable"]["Properties"][0]
        except (DownloadError, ValueError, KeyError, IndexError, TypeError):
            return {}

    @staticmethod
    def _looks_like_smiles(query: str) -> bool:
        if " " in query or len(query) > 300:
            return False
        return bool(re.search(r"[A-Za-z0-9@+\-\[\]\(\)=#$.%]", query)) and not re.match(
            r"^[A-Za-z][A-Za-z0-9 '\-]+$", query
        )

    # ------------------------------------------------------------------
    # RCSB PDB
    # ------------------------------------------------------------------
    def pdb(
        self, pdb_id: str, *, cancel: threading.Event | None = None
    ) -> DownloadResult:
        _raise_if_cancelled(cancel)
        pdb_id = _clean_text(pdb_id).upper()
        if not _PDB_ID.match(pdb_id):
            raise DownloadError("PDB ID 应为 4 位字符，例如 1BCI、6LU7。")

        warnings: list[str] = []
        try:
            response = self._get(f"{RCSB_FILES}/{pdb_id}.pdb")
            data = response.content
            suffix = ".pdb"
        except DownloadCancelled:
            raise
        except DownloadError:
            _raise_if_cancelled(cancel)
            response = self._get(f"{RCSB_FILES}/{pdb_id}.cif")
            data = response.content
            suffix = ".cif"
            warnings.append("该结构的 PDB 格式不可用，已下载 mmCIF 格式。")

        if suffix == ".pdb" and b"ATOM" not in data and b"HETATM" not in data:
            raise DownloadError("下载的文件不包含原子坐标，请确认 PDB ID。")
        if suffix == ".cif" and b"_atom_site." not in data:
            raise DownloadError("下载的文件不是有效的 mmCIF 结构。")

        _raise_if_cancelled(cancel)
        info = self._rcsb_metadata(pdb_id)
        title = _clean_text(info.get("title"), pdb_id)
        resolution = info.get("resolution")
        meta = {
            "source": "RCSB PDB",
            "pdb_id": pdb_id,
            "url": f"https://www.rcsb.org/structure/{pdb_id}",
            "title": title,
            "resolution": resolution,
            "format": suffix.lstrip("."),
            "warnings": warnings,
        }
        return DownloadResult(
            data=data,
            filename=_safe_filename(f"pdb_{pdb_id}", "pdb_structure") + suffix,
            display_name=f"{pdb_id} · {title}" if title != pdb_id else pdb_id,
            meta=meta,
        )

    def _rcsb_metadata(self, pdb_id: str) -> dict:
        try:
            response = self._get(f"{RCSB_DATA}/{pdb_id}", accept="application/json")
            payload = response.json()
            title = _clean_text(payload.get("struct", {}).get("title"))
            resolutions = payload.get("rcsb_entry_info", {}).get("resolution_combined") or []
            resolution = round(float(resolutions[0]), 2) if resolutions else None
            return {"title": title, "resolution": resolution}
        except (DownloadError, ValueError, TypeError, IndexError):
            return {}

    # ------------------------------------------------------------------
    # AlphaFold
    # ------------------------------------------------------------------
    def alphafold(
        self, accession: str, *, cancel: threading.Event | None = None
    ) -> DownloadResult:
        _raise_if_cancelled(cancel)
        accession = _clean_text(accession).upper()
        if not _UNIPROT.match(accession):
            raise DownloadError("请输入 UniProt 登录号，例如 P00533。")

        pdb_url = ""
        meta: dict = {}
        try:
            response = self._get(
                f"{ALPHAFOLD_API}/{accession}", accept="application/json"
            )
            entries = response.json()
            first = entries[0] if isinstance(entries, list) and entries else {}
            pdb_url = _clean_text(first.get("pdbUrl"))
            meta = {
                "uniprot": accession,
                "entry_id": _clean_text(first.get("entryId")),
                "sequence_start": first.get("uniprotStart"),
                "sequence_end": first.get("uniprotEnd"),
            }
        except DownloadCancelled:
            raise
        except (DownloadError, ValueError, TypeError, KeyError, IndexError):
            pass

        _raise_if_cancelled(cancel)
        if not pdb_url:
            pdb_url = f"{ALPHAFOLD_FILES}/AF-{accession}-F1-model_v4.pdb"
        try:
            response = self._get(pdb_url)
            data = response.content
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            raise DownloadError(f"AlphaFold 没有与 {accession} 匹配的预测结构。") from exc
        if b"ATOM" not in data:
            raise DownloadError("AlphaFold 返回的文件不包含原子坐标。")

        meta.update(
            {
                "source": "AlphaFold DB",
                "url": f"https://alphafold.ebi.ac.uk/entry/{accession}",
                "warnings": [
                    "AlphaFold 结构是计算预测模型，不含实验配体；对接结果应谨慎解释。"
                ],
            }
        )
        return DownloadResult(
            data=data,
            filename=_safe_filename(f"alphafold_{accession}", "alphafold_model") + ".pdb",
            display_name=f"{accession} · AlphaFold 预测结构",
            meta=meta,
        )

    # ------------------------------------------------------------------
    # ChEMBL
    # ------------------------------------------------------------------
    def chembl(
        self, query: str, *, cancel: threading.Event | None = None
    ) -> DownloadResult:
        _raise_if_cancelled(cancel)
        query = _clean_text(query)
        if not query:
            raise DownloadError("请输入 ChEMBL ID 或化合物名称。")

        chembl_id = query.upper() if _CHEMBL_ID.match(query) else ""
        pref_name = ""
        if not chembl_id:
            encoded = quote(query, safe="")
            try:
                response = self._get(
                    f"{CHEMBL}/molecule/search",
                    params={"q": query, "format": "json", "limit": 1},
                )
                molecules = response.json().get("molecules") or []
                if not molecules:
                    raise DownloadError(f"ChEMBL 没有找到名为“{query}”的化合物。")
                first = molecules[0]
                chembl_id = _clean_text(first.get("molecule_chembl_id"))
                pref_name = _clean_text(first.get("pref_name"))
            except DownloadError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise DownloadError("ChEMBL 搜索返回了无法解析的内容。") from exc

        _raise_if_cancelled(cancel)
        response = self._get(f"{CHEMBL}/molecule/{quote(chembl_id, safe='')}", params={"format": "sdf"})
        data = response.content
        if b"V2000" not in data and b"V3000" not in data:
            raise DownloadError("ChEMBL 返回的内容不是有效的 SDF 文件。")
        if b"$$$$" not in data:
            data = data.rstrip() + b"\n$$$$\n"

        return DownloadResult(
            data=data,
            filename=_safe_filename(f"chembl_{chembl_id}", "chembl_ligand") + ".sdf",
            display_name=pref_name or chembl_id,
            meta={
                "source": "ChEMBL",
                "chembl_id": chembl_id,
                "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/",
                "warnings": [],
            },
        )


def download_file(
    source: str,
    query: str,
    destination: str | Path,
    settings: AppSettings | None = None,
    *,
    cancel: threading.Event | None = None,
) -> tuple[Path, DownloadResult]:
    """下载并写入目标路径，返回实际路径与下载信息。"""
    source = source.strip().lower()
    client = DownloadClient(settings)
    dispatch = {
        "pubchem": client.pubchem,
        "pdb": client.pdb,
        "rcsb": client.pdb,
        "alphafold": client.alphafold,
        "chembl": client.chembl,
    }
    if source not in dispatch:
        raise DownloadError(f"未知的下载来源：{source}")
    result = dispatch[source](query, cancel=cancel)

    destination = Path(destination)
    if destination.is_dir():
        destination = destination / result.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result.data)
    log.info("已下载 %s → %s", result.meta.get("url", source), destination)
    return destination, result
