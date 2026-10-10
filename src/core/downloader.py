#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
standalone_downloader.py
完全独立的配体/受体下载工具 —— 无需外部模块依赖
用法:
    python standalone_downloader.py --help
    python standalone_downloader.py ligand  --id 2244 --db PubChem --out aspirin.sdf
    python standalone_downloader.py receptor --id 1HVR --db RCSB --out 1HVR.pdb
    python standalone_downloader.py batch-ligand  --list ligands.txt --outdir ./ligands/
    python standalone_downloader.py batch-receptor --list receptors.txt --outdir ./receptors/
"""

import os
import re
import sys
import time
import json
import logging
import argparse
import threading
import urllib.request
import urllib.error
import urllib.parse
import subprocess
from collections import Counter
from datetime import datetime

# ─────────────────────────── 日志 ───────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("downloader")

_log_lock = threading.Lock()

def log(msg: str, level: str = "INFO", log_text=None):
    level = level.upper()
    lvl = getattr(logging, level, logging.INFO)
    with _log_lock:
        logger.log(lvl, msg)
    if log_text is not None and hasattr(log_text, "append"):
        log_text.append(f"[{level}] {msg}")
    try:
        from src.core.logger import get_log_callback
        cb = get_log_callback()
        if cb is not None:
            cb(f"[{level}] {msg}")
    except Exception:
        pass


# ─────────────────────────── 工具函数 ───────────────────────────
def ensure_dir(dir_path: str, log_text=None) -> str:
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
        log(f"创建目录: {dir_path}", log_text=log_text)
    return dir_path


def run_command(cmd: str, env=None, capture_output=False):
    """执行系统命令，返回 (stdout, success)"""
    try:
        result = subprocess.run(
            cmd, shell=True, env=env,
            capture_output=capture_output, text=True, timeout=120
        )
        return result.stdout, result.returncode == 0
    except Exception as e:
        log(f"命令执行异常: {e}", "WARNING")
        return "", False


def http_get(url: str, timeout: int = 30, headers: dict = None) -> tuple:
    """
    简单 HTTP GET，返回 (content_str, status_code)
    不依赖 requests，使用标准库 urllib
    """
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.rcsb.org/",
    }
    if headers:
        default_headers.update(headers)

    req = urllib.request.Request(url, headers=default_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            return content, resp.status
    except urllib.error.HTTPError as e:
        return "", e.code
    except Exception as e:
        raise ConnectionError(str(e))


# ─────────────────────────── 内置查询模块 ───────────────────────────
class QueryModule:
    """
    通过 PubChem / RCSB / UniProt / TCMSP 实时查询 ID
    完全替代外部 EnhancedQueryModule
    """

    def query_ligand(self, name: str, db: str = "PubChem") -> list:
        """查询配体，返回 [{'id': ..., 'db': ...}, ...]"""
        clean = name.strip()
        if db == "PubChem":
            return self._query_pubchem(clean)
        elif db == "TCMSP":
            return self._query_tcmsp(clean)
        elif db == "ChEMBL":
            return self._query_chembl(clean)
        return []

    def query_protein(self, name: str, db: str = "RCSB PDB") -> list:
        """查询蛋白受体，返回 [{'id': ..., 'db': ...}, ...]"""
        clean = name.strip()
        if "rcsb" in db.lower() or "pdb" in db.lower():
            return self._query_rcsb(clean)
        elif "alphafold" in db.lower():
            return self._query_alphafold(clean)
        return []

    # ── PubChem ──
    def _query_pubchem(self, name: str) -> list:
        encoded = urllib.parse.quote(name)
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{encoded}/cids/JSON"
        )
        try:
            content, status = http_get(url)
            if status == 200 and content:
                data = json.loads(content)
                cids = data.get("IdentifierList", {}).get("CID", [])
                return [{"id": str(c), "db": "PubChem"} for c in cids[:5]]
        except Exception as e:
            log(f"PubChem 查询异常: {e}", "WARNING")
        return []

    # ── ChEMBL ──
    def _query_chembl(self, name: str) -> list:
        encoded = urllib.parse.quote(name)
        url = (
            f"https://www.ebi.ac.uk/chembl/api/data/molecule/search"
            f"?q={encoded}&format=json&limit=5"
        )
        try:
            content, status = http_get(url)
            if status == 200 and content:
                data = json.loads(content)
                mols = data.get("molecules", [])
                return [{"id": m["molecule_chembl_id"], "db": "ChEMBL"} for m in mols]
        except Exception as e:
            log(f"ChEMBL 查询异常: {e}", "WARNING")
        return []

    # ── TCMSP ──
    def _query_tcmsp(self, name: str) -> list:
        encoded = urllib.parse.quote(name)
        url = f"http://tcmspw.com/tcmspsearch.php?qr={encoded}&qsr=herb_name&q=1"
        try:
            content, status = http_get(url, timeout=20)
            if status == 200:
                mol_ids = re.findall(r"MOL\d{6}", content)
                return [{"id": m, "db": "TCMSP"} for m in dict.fromkeys(mol_ids)[:5]]
        except Exception as e:
            log(f"TCMSP 查询异常: {e}", "WARNING")
        return []

    # ── RCSB PDB ──
    def _query_rcsb(self, name: str) -> list:
        # 先尝试直接 4 字符 PDB ID
        if len(name) == 4 and name.isalnum():
            return [{"id": name.upper(), "db": "RCSB PDB"}]
        # 否则用全文搜索接口
        query = {
            "query": {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": name}
            },
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": 5}}
        }
        url = "https://search.rcsb.org/rcsbsearch/v2/query"
        try:
            data_bytes = json.dumps(query).encode()
            req = urllib.request.Request(
                url, data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                content = resp.read().decode()
            data = json.loads(content)
            results = data.get("result_set", [])
            return [{"id": r["identifier"], "db": "RCSB PDB"} for r in results]
        except Exception as e:
            log(f"RCSB 查询异常: {e}", "WARNING")
        return []

    # ── AlphaFold / UniProt ──
    def _query_alphafold(self, name: str) -> list:
        encoded = urllib.parse.quote(name)
        url = (
            f"https://rest.uniprot.org/uniprotkb/search"
            f"?query={encoded}&fields=accession&format=json&size=5"
        )
        try:
            content, status = http_get(url, timeout=20)
            if status == 200 and content:
                data = json.loads(content)
                results = data.get("results", [])
                return [
                    {"id": r["primaryAccession"], "db": "AlphaFold DB"}
                    for r in results
                ]
        except Exception as e:
            log(f"UniProt/AlphaFold 查询异常: {e}", "WARNING")
        return []


# ─────────────────────────── 下载核心 ───────────────────────────
class Downloader:
    """
    完全自包含的下载器，涵盖：
      配体: PubChem / ChEMBL / ZINC / TCMSP
      受体: RCSB PDB / AlphaFold DB
    """

    def __init__(self, max_retries: int = 3, retry_delay: int = 5, timeout: int = 30):
        self.cfg = {
            "max_retries": max_retries,
            "retry_delay": retry_delay,
            "timeout": timeout,
        }

    # ═══════════════ 配体下载 ═══════════════
    def download_ligand(self, identifier: str, db: str, save_path: str) -> bool:
        log(f"开始下载配体: {identifier} (数据库: {db})", "DEBUG")
        db_map = {
            "pubchem": self.download_from_pubchem,
            "chembl":  self.download_from_chembl,
            "zinc":    self.download_from_zinc,
            "tcmsp":   self.download_from_tcmsp,
        }
        func = db_map.get(db.lower(), self.download_from_pubchem)
        return func(identifier, save_path)

    def download_from_pubchem(self, cid: str, save_path: str) -> bool:
        clean = str(cid).strip()
        if not clean.isdigit():
            nums = re.findall(r"\d+", clean)
            if nums:
                clean = nums[0]
                log(f"清理 CID: {cid} → {clean}", "DEBUG")
            else:
                log(f"❌ 无效 PubChem CID: {cid}", "ERROR")
                return False
        mirrors = [
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{clean}/SDF?record_type=3d",
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{clean}/SDF",
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{clean}/record/SDF",
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{clean}/SDF?record_type=2d",
        ]
        for idx, url in enumerate(mirrors, 1):
            log(f"尝试 PubChem 镜像 {idx}/{len(mirrors)}")
            if self._download(url, save_path, f"PubChem_mirror_{idx}"):
                return True
        return False

    def download_from_chembl(self, chembl_id: str, save_path: str) -> bool:
        clean = str(chembl_id).strip().upper()
        if not clean.startswith("CHEMBL"):
            log(f"❌ 无效 ChEMBL ID: {chembl_id}", "ERROR")
            return False
        url = f"https://www.ebi.ac.uk/chembl/api/data/molecule/{clean}.sdf"
        return self._download(url, save_path, "ChEMBL")

    def download_from_zinc(self, zinc_id: str, save_path: str) -> bool:
        clean = str(zinc_id).strip().upper()
        if not clean.startswith("ZINC"):
            log(f"❌ 无效 ZINC ID: {zinc_id}", "ERROR")
            return False
        url = f"http://zinc15.docking.org/substances/{clean}.sdf"
        return self._download(url, save_path, "ZINC")

    def download_from_tcmsp(self, mol_id: str, save_path: str) -> bool:
        url = f"http://tcmspw.com/tcmspdownload.php?mol_id={mol_id}"
        try:
            content, status = http_get(url, timeout=30)
            if status == 200 and content:
                ensure_dir(os.path.dirname(save_path) or ".")
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(content)
                if os.path.getsize(save_path) > 100:
                    return True
                log("TCMSP 下载文件太小", "WARNING")
            else:
                log(f"TCMSP 状态码: {status}", "WARNING")
        except Exception as e:
            log(f"TCMSP 下载异常: {e}", "WARNING")
        return False

    def download_by_name(self, name: str, save_path: str, db: str = "PubChem") -> bool:
        encoded = urllib.parse.quote(name)
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/SDF"
        )
        return self._download(url, save_path, "PubChem_by_name")

    # ═══════════════ 受体下载 ═══════════════
    def download_receptor(self, identifier: str, db: str, save_path: str) -> bool:
        log(f"开始下载受体: {identifier} (数据库: {db})", "DEBUG")
        db_lower = db.lower()
        if "rcsb" in db_lower or "pdb" in db_lower:
            return self.download_from_pdb(identifier, save_path)
        elif "alphafold" in db_lower:
            return self.download_from_alphafold(identifier, save_path)
        log(f"未知数据库 {db}，默认 RCSB PDB", "WARNING")
        return self.download_from_pdb(identifier, save_path)

    def download_from_pdb(self, pdb_id: str, save_path: str) -> bool:
        pdb_id = pdb_id.strip().upper()
        if len(pdb_id) != 4 or not pdb_id.isalnum():
            log(f"❌ 无效 PDB ID: {pdb_id}", "ERROR")
            return False
        mirrors = [
            f"https://files.rcsb.org/download/{pdb_id}.pdb",
            f"https://cdn.rcsb.org/resources/files/{pdb_id}.pdb",
        ]
        for idx, url in enumerate(mirrors, 1):
            log(f"尝试 PDB 镜像 {idx}/{len(mirrors)}: {url}")
            if self._download(url, save_path, f"PDB_mirror_{idx}"):
                return True
        # CIF 回落
        log("所有 PDB 镜像失败，尝试 CIF 回落", "INFO")
        cif_url = f"https://files.rcsb.org/download/{pdb_id}.cif"
        temp_cif = save_path.replace(".pdb", "_temp.cif")
        if self._download(cif_url, temp_cif, "CIF"):
            cmd = f'obabel "{temp_cif}" -O "{save_path}" -opdb -d'
            _, ok = run_command(cmd, capture_output=True)
            if os.path.exists(temp_cif):
                os.remove(temp_cif)
            if ok and os.path.exists(save_path) and os.path.getsize(save_path) > 100:
                log(f"✅ CIF 转 PDB 成功: {pdb_id}", "INFO")
                return True
        log(f"❌ 所有方法均失败: {pdb_id}", "ERROR")
        return False

    def download_from_alphafold(self, uniprot_id: str, save_path: str) -> bool:
        clean = str(uniprot_id).strip().upper()
        if not clean[0] in "PQOA":
            log(f"❌ 无效 UniProt ID: {uniprot_id}", "ERROR")
            return False
        for ver in ["v6", "v5", "v4", "v3", "v2"]:
            url = (
                f"https://alphafold.ebi.ac.uk/files/"
                f"AF-{clean}-F1-model_{ver}.pdb"
            )
            log(f"尝试 AlphaFold {ver}: {url}")
            if self._download(url, save_path, f"AlphaFold_{ver}"):
                log(f"✅ 成功使用版本: {ver}")
                return True
        log(f"❌ 所有 AlphaFold 版本均不可用: {uniprot_id}", "ERROR")
        return False

    # ═══════════════ 核心下载 & 验证 ═══════════════
    def _download(self, url: str, save_path: str, source: str = "Unknown") -> bool:
        for attempt in range(self.cfg["max_retries"]):
            try:
                log(f"  下载尝试 {attempt+1}/{self.cfg['max_retries']}: {source}", "DEBUG")
                content, status = http_get(url, timeout=self.cfg["timeout"])
                if status == 200:
                    if self._validate_content(content, source):
                        ensure_dir(os.path.dirname(save_path) or ".")
                        with open(save_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        if (
                            os.path.getsize(save_path) > 100
                            and self._validate_content(
                                open(save_path, encoding="utf-8").read(), source
                            )
                        ):
                            size_kb = os.path.getsize(save_path) / 1024
                            log(f"  ✅ {source} 下载成功: {size_kb:.1f}KB", "DEBUG")
                            return True
                        log("  ❌ 文件内容无效", "WARNING")
                        if os.path.exists(save_path):
                            os.remove(save_path)
                    else:
                        log("  ❌ 内容格式无效", "WARNING")
                elif status in (403, 429):
                    log(f"  ⚠️ 被拒绝 ({status})，跳过此源", "WARNING")
                    return False
                else:
                    log(f"  ❌ 状态码 {status}，重试...", "WARNING")
            except ConnectionError as e:
                log(f"  ⚠️ 网络异常: {e}，重试...", "WARNING")
            if attempt < self.cfg["max_retries"] - 1:
                delay = self.cfg["retry_delay"] * (2 ** attempt)
                log(f"  等待 {delay}s 后重试...", "DEBUG")
                time.sleep(delay)
        log("  ❌ 所有尝试均失败", "ERROR")
        return False

    def _validate_content(self, content: str, source: str) -> bool:
        if not content or len(content.strip()) < 100:
            return False
        src = source.lower()
        if any(k in src for k in ["pubchem", "chembl", "zinc", "tcmsp"]):
            return any(k in content for k in ["$$$$", "END", "V2000"])
        if any(k in src for k in ["pdb", "alphafold", "cif"]):
            return any(k in content for k in ["ATOM", "HEADER", "TITLE", "_atom"])
        return len(content) > 100

    # ═══════════════ 批量接口 ═══════════════
    def batch_download_ligands(
        self, ligand_list: list, output_dir: str, log_text=None
    ) -> dict:
        """
        ligand_list: [(name, identifier, db), ...]
        """
        results = {"success": [], "failed": []}
        ensure_dir(output_dir)
        for name, identifier, db in ligand_list:
            save_path = os.path.join(output_dir, f"{name}.sdf")
            if self.download_ligand(identifier, db, save_path):
                results["success"].append((name, save_path))
                log(f"✅ 成功下载: {name}", log_text=log_text)
            else:
                results["failed"].append((name, identifier, db))
                log(f"❌ 下载失败: {name}", "ERROR", log_text)
        return results

    def batch_download_receptors(
        self, receptor_list: list, output_dir: str, log_text=None
    ) -> dict:
        """
        receptor_list: [(name, identifier, db), ...]
        """
        results = {"success": [], "failed": []}
        ensure_dir(output_dir)
        for name, identifier, db in receptor_list:
            save_path = os.path.join(output_dir, f"{name}.pdb")
            if self.download_receptor(identifier, db, save_path):
                results["success"].append((name, save_path))
                log(f"✅ 成功下载: {name}", log_text=log_text)
            else:
                results["failed"].append((name, identifier, db))
                log(f"❌ 下载失败: {name}", "ERROR", log_text)
        return results


# ─────────────────────────── 列表文件读取 ───────────────────────────
_BAD_KEYWORDS = ["失败", "错误", "超时", "不支持", "未找到", "查询异常"]


def read_download_list(list_path: str, list_type: str = "ligand", log_text=None) -> list:
    """
    读取下载列表文件，每行格式: <name> [id] [db]
    返回 [(name, id_val, db, line_num), ...]
    """
    if not os.path.exists(list_path):
        log(f"❌ 未找到列表文件: {list_path}", "ERROR", log_text)
        return []
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(list_path, "r", encoding=enc) as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            break
        except UnicodeDecodeError:
            continue
    else:
        log("❌ 无法解码列表文件", "ERROR", log_text)
        return []

    items = []
    for line_num, line in enumerate(lines, 1):
        # Support both comma-separated and whitespace-separated formats
        if ',' in line:
            parts = [p.strip() for p in line.split(',') if p.strip()]
        else:
            parts = line.split()
        if not parts:
            continue
        name = parts[0]
        id_val = parts[1] if len(parts) >= 2 else ""
        if any(k in id_val for k in _BAD_KEYWORDS):
            id_val = ""
        db = parts[2] if len(parts) >= 3 else "Auto"
        # 标准化数据库名
        if list_type == "ligand":
            db_norm = {
                "pubchem": "PubChem", "pdb": "RCSB PDB",
                "chembl": "ChEMBL", "tcmsp": "TCMSP", "zinc": "ZINC"
            }.get(db.lower(), db)
        else:
            db_norm = {
                "rcsb": "RCSB PDB", "rcsb pdb": "RCSB PDB", "pdb": "RCSB PDB",
                "alphafold": "AlphaFold DB", "alphafold db": "AlphaFold DB"
            }.get(db.lower(), db)
        items.append((name, id_val, db_norm, line_num))
        log(f"  读取: {name} | {id_val or '(待查询)'} | {db_norm}", log_text=log_text)
    log(f"共读取 {len(items)} 条 {list_type} 记录", log_text=log_text)
    return items


# ─────────────────────────── 自动 ID 查询 ───────────────────────────
_qm = QueryModule()


def auto_query_ligand_id(name: str, log_text=None):
    """实时查询配体 ID，返回 (id, db) 或 (None, None)"""
    log(f"🔍 查询配体 ID: {name}", log_text=log_text)
    clean = name.strip()
    # 快速规则识别
    if clean.upper().startswith("MOL") and len(clean) >= 7:
        return clean.upper(), "TCMSP"
    if clean.isdigit() and len(clean) >= 3:
        return clean, "PubChem"
    # PDB ID 模式（4字符数字+字母混合，如 6VZ1, 9CYO）
    if len(clean) == 4 and clean[0].isdigit() and clean.isalnum():
        results = [
            r for r in _qm.query_protein(clean, "RCSB PDB")
            if not any(k in str(r.get("id", "")) for k in _BAD_KEYWORDS)
        ]
        if results:
            log(f"✅ RCSB PDB: {clean} → {results[0]['id']}", log_text=log_text)
            return str(results[0]["id"]), "RCSB PDB"
    # PubChem
    results = [
        r for r in _qm.query_ligand(clean, "PubChem")
        if not any(k in str(r.get("id", "")) for k in _BAD_KEYWORDS)
    ]
    if results:
        log(f"✅ PubChem: {clean} → CID {results[0]['id']}", log_text=log_text)
        return str(results[0]["id"]), "PubChem"
    # TCMSP
    results = [
        r for r in _qm.query_ligand(clean, "TCMSP")
        if not any(k in str(r.get("id", "")) for k in _BAD_KEYWORDS)
    ]
    if results:
        log(f"✅ TCMSP: {clean} → {results[0]['id']}", log_text=log_text)
        return str(results[0]["id"]), "TCMSP"
    # RCSB PDB 回退（非标准 PDB ID 格式）
    results = [
        r for r in _qm.query_protein(clean, "RCSB PDB")
        if not any(k in str(r.get("id", "")) for k in _BAD_KEYWORDS)
    ]
    if results:
        log(f"✅ RCSB PDB: {clean} → {results[0]['id']}", log_text=log_text)
        return str(results[0]["id"]), "RCSB PDB"
    log(f"❌ 未找到配体 ID: {clean}", "ERROR", log_text)
    return None, None


def auto_query_receptor_id(name: str, log_text=None):
    """实时查询受体 ID，返回 (id, db) 或 (None, None)"""
    log(f"🔍 查询受体 ID: {name}", log_text=log_text)
    for db in ["RCSB PDB", "AlphaFold DB"]:
        results = [
            r for r in _qm.query_protein(name, db)
            if not any(k in str(r.get("id", "")) for k in _BAD_KEYWORDS)
        ]
        if results:
            rid, rdb = results[0]["id"], results[0]["db"]
            log(f"✅ {rdb}: {name} → {rid}", log_text=log_text)
            return rid, rdb
    log(f"❌ 未找到受体 ID: {name}", "ERROR", log_text)
    return None, None


# ─────────────────────────── 批量下载流程 ───────────────────────────
def download_ligands_from_list(list_path: str = None, output_dir: str = None, log_text=None) -> bool:
    from src.core.config import CONFIG
    from src.utils.path import get_path
    if list_path is None:
        list_path = get_path("Ligand_list.txt")
    if output_dir is None:
        output_dir = CONFIG.get("2.原始配体", get_path("2.原始配体"))
    log("=== 开始批量下载配体 ===", log_text=log_text)
    ensure_dir(output_dir, log_text)
    items = read_download_list(list_path, "配体", log_text)
    if not items:
        log("无有效配体列表，终止", log_text=log_text)
        return False

    dl = Downloader()
    success_count = 0
    for name, identifier, db, line_num in items:
        # 补全 ID
        if not identifier or any(k in identifier for k in _BAD_KEYWORDS):
            identifier, queried_db = auto_query_ligand_id(name, log_text)
            if queried_db:
                db = queried_db
        if not identifier:
            log(f"[{line_num}] ❌ 跳过（无法获取 ID）: {name}", "ERROR", log_text)
            continue
        # 推断数据库
        if db in ("Auto", ""):
            if identifier.isdigit():
                db = "PubChem"
            elif identifier.startswith("CHEMBL"):
                db = "ChEMBL"
            elif identifier.startswith("ZINC"):
                db = "ZINC"
            elif len(identifier) == 4 and identifier[0].isdigit() and identifier.isalnum():
                db = "RCSB PDB"
            else:
                db = "PubChem"
        # PDB 下载
        if db == "RCSB PDB":
            pdb_save = os.path.join(output_dir, f"{name}.pdb")
            if os.path.exists(pdb_save) and os.path.getsize(pdb_save) > 100:
                log(f"[{line_num}] ✓ 已存在: {name}.pdb", log_text=log_text)
                success_count += 1
                continue
            log(f"[{line_num}] 下载: {name} → {identifier} ({db})", log_text=log_text)
            if dl.download_receptor(identifier, db, pdb_save):
                log(f"[{line_num}] ✅ 成功: {name}.pdb", log_text=log_text)
                success_count += 1
            else:
                log(f"[{line_num}] ❌ 失败: {name}", "ERROR", log_text)
            continue
        # ID 格式修正
        if db == "PubChem" and not identifier.isdigit():
            clean_id = re.sub(r"\D", "", identifier)
            if clean_id:
                identifier = clean_id
            else:
                log(f"[{line_num}] ❌ 无法清理 ID，跳过: {identifier}", "ERROR", log_text)
                continue
        save_path = os.path.join(output_dir, f"{name}.sdf")
        if os.path.exists(save_path) and os.path.getsize(save_path) > 100:
            log(f"[{line_num}] ✓ 已存在: {name}.sdf", log_text=log_text)
            success_count += 1
            continue
        log(f"[{line_num}] 下载: {name} → {identifier} ({db})", log_text=log_text)
        if dl.download_ligand(identifier, db, save_path):
            log(f"[{line_num}] ✅ 成功: {name}.sdf", log_text=log_text)
            success_count += 1
        else:
            log(f"[{line_num}] ❌ 失败: {name}", "ERROR", log_text)

    log(f"=== 配体下载完成: {success_count}/{len(items)} ===", log_text=log_text)
    return success_count > 0


def download_receptors_from_list(list_path: str = None, output_dir: str = None, log_text=None) -> bool:
    from src.core.config import CONFIG
    from src.utils.path import get_path
    if list_path is None:
        list_path = get_path("Receptor_list.txt")
    if output_dir is None:
        output_dir = CONFIG.get("1.原始受体", get_path("1.原始受体"))
    log("=== 开始批量下载受体 ===", log_text=log_text)
    ensure_dir(output_dir, log_text)
    items = read_download_list(list_path, "受体", log_text)
    if not items:
        log("无有效受体列表，终止", log_text=log_text)
        return False

    dl = Downloader()
    name_counter = Counter(n for n, _, _, _ in items)
    success_count = 0
    for name, identifier, db, line_num in items:
        if not identifier:
            identifier, auto_db = auto_query_receptor_id(name, log_text)
            if not identifier:
                log(f"[{line_num}] ❌ 跳过（无法获取 ID）: {name}", "ERROR", log_text)
                continue
            if db in ("Auto", ""):
                db = auto_db
        if db in ("Auto", ""):
            db = (
                "RCSB PDB" if len(identifier) == 4 and identifier.isalnum()
                else "AlphaFold DB" if identifier[0] in "PQOA"
                else "RCSB PDB"
            )
        save_name = f"{name}_{identifier}" if name_counter[name] > 1 else name
        save_path = os.path.join(output_dir, f"{save_name}.pdb")
        if os.path.exists(save_path) and os.path.getsize(save_path) > 100:
            log(f"[{line_num}] ✓ 已存在: {save_name}.pdb", log_text=log_text)
            success_count += 1
            continue
        log(f"[{line_num}] 下载: {name} → {identifier} ({db})", log_text=log_text)
        if dl.download_receptor(identifier, db, save_path):
            log(f"[{line_num}] ✅ 成功: {save_name}.pdb", log_text=log_text)
            success_count += 1
        else:
            log(f"[{line_num}] ❌ 失败: {name}", "ERROR", log_text)

    log(f"=== 受体下载完成: {success_count}/{len(items)} ===", log_text=log_text)
    return success_count > 0


# ─────────────────────────── CLI ───────────────────────────
def build_parser():
    p = argparse.ArgumentParser(
        description="standalone_downloader —— 配体/受体下载工具（单文件，无外部模块依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单个配体
  python standalone_downloader.py ligand --id 2244 --db PubChem --out aspirin.sdf

  # 单个受体
  python standalone_downloader.py receptor --id 1HVR --db RCSB --out 1HVR.pdb

  # 批量配体（列表文件每行: name [id] [db]）
  python standalone_downloader.py batch-ligand --list ligands.txt --outdir ./ligands/

  # 批量受体
  python standalone_downloader.py batch-receptor --list receptors.txt --outdir ./receptors/
        """
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ── ligand ──
    lg = sub.add_parser("ligand", help="下载单个配体")
    lg.add_argument("--id",  required=True, help="分子 ID（CID / CHEMBL... / ZINC...）")
    lg.add_argument("--db",  default="PubChem",
                    choices=["PubChem", "ChEMBL", "ZINC", "TCMSP"],
                    help="数据库（默认 PubChem）")
    lg.add_argument("--out", required=True, help="输出文件路径（.sdf / .mol2）")

    # ── receptor ──
    rp = sub.add_parser("receptor", help="下载单个受体")
    rp.add_argument("--id",  required=True, help="PDB ID 或 UniProt ID")
    rp.add_argument("--db",  default="RCSB",
                    choices=["RCSB", "RCSB PDB", "AlphaFold", "AlphaFold DB"],
                    help="数据库（默认 RCSB PDB）")
    rp.add_argument("--out", required=True, help="输出文件路径（.pdb）")

    # ── batch-ligand ──
    bl = sub.add_parser("batch-ligand", help="批量下载配体（列表文件）")
    bl.add_argument("--list",   required=True, help="配体列表文件")
    bl.add_argument("--outdir", required=True, help="输出目录")

    # ── batch-receptor ──
    br = sub.add_parser("batch-receptor", help="批量下载受体（列表文件）")
    br.add_argument("--list",   required=True, help="受体列表文件")
    br.add_argument("--outdir", required=True, help="输出目录")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    dl = Downloader()

    if args.cmd == "ligand":
        ok = dl.download_ligand(args.id, args.db, args.out)
        sys.exit(0 if ok else 1)

    elif args.cmd == "receptor":
        ok = dl.download_receptor(args.id, args.db, args.out)
        sys.exit(0 if ok else 1)

    elif args.cmd == "batch-ligand":
        ok = download_ligands_from_list(args.list, args.outdir)
        sys.exit(0 if ok else 1)

    elif args.cmd == "batch-receptor":
        ok = download_receptors_from_list(args.list, args.outdir)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
