"""OpenBabel 适配层。

优先使用 OpenBabel 的 Python API（可正确处理中文路径）；若 Python 绑定不可用，
则回退到 ``obabel`` 命令行，并在 ASCII 临时目录中转换以规避路径编码问题。

支持三种安装形态：
* conda：``<env>/Library/bin`` + ``<env>/Library/share/openbabel``（Windows）
* openbabel-wheel：``site-packages/openbabel/bin`` + ``bin/data``
* 系统安装（Homebrew / Linux 发行版）
"""

from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

from ..errors import DependencyError
from ..logging_setup import get_logger
from .runtime import IS_MACOS, IS_WINDOWS, find_executable, run_process, work_directory

log = get_logger("obabel")

_LOCK = threading.RLock()
_configured = False


def _has_data_files(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return (directory / "element.txt").is_file() or bool(
        list(directory.glob("bondtyp.txt"))
    )


def _conda_layouts() -> list[tuple[Path, Path]]:
    layouts: list[tuple[Path, Path]] = []
    prefixes = {Path(sys.prefix), Path(sys.executable).resolve().parent}
    for prefix in prefixes:
        if IS_WINDOWS:
            lib_bin = prefix / "Library" / "bin"
            share = prefix / "Library" / "share" / "openbabel"
        else:
            lib_bin = prefix / "bin"
            share = prefix / "share" / "openbabel"
        for data in sorted(share.glob("*"), reverse=True) if share.is_dir() else []:
            if _has_data_files(data):
                layouts.append((lib_bin, data))
        if share.is_dir() and _has_data_files(share):
            layouts.append((lib_bin, share))
    return layouts


def _wheel_layouts() -> list[tuple[Path, Path]]:
    layouts: list[tuple[Path, Path]] = []
    try:
        spec = importlib.util.find_spec("openbabel")
    except (ImportError, ValueError):
        return layouts
    if spec is None:
        return layouts
    bases: list[Path] = []
    if spec.submodule_search_locations:
        bases.extend(Path(p) for p in spec.submodule_search_locations)
    elif spec.origin:
        bases.append(Path(spec.origin).resolve().parent)
    for base in bases:
        bin_dir = base / "bin"
        data_dir = bin_dir / "data"
        if _has_data_files(data_dir):
            layouts.append((bin_dir, data_dir))
        if _has_data_files(base):
            layouts.append((base, base))
    return layouts


def _bundle_roots() -> list[Path]:
    """PyInstaller 运行时内置的 OpenBabel 目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return []
    base = Path(meipass)
    return [base / "openbabel", base / "dockflowlab" / "resources" / "openbabel"]


def _bundle_layouts() -> list[tuple[Path, Path]]:
    layouts: list[tuple[Path, Path]] = []
    for root in _bundle_roots():
        bin_dir = root / "bin"
        data_dir = bin_dir / "data"
        if _has_data_files(data_dir):
            layouts.append((bin_dir, data_dir))
        elif _has_data_files(root):
            layouts.append((root, root))
    return layouts


def _system_layouts() -> list[tuple[Path, Path]]:
    layouts: list[tuple[Path, Path]] = []
    executable = shutil.which("obabel")
    if executable:
        prefix = Path(executable).resolve().parent.parent
        share = prefix / "share" / "openbabel"
        for data in sorted(share.glob("*"), reverse=True) if share.is_dir() else []:
            if _has_data_files(data):
                layouts.append((Path(executable).resolve().parent, data))
    for prefix in (Path("/opt/homebrew"), Path("/usr/local"), Path("/usr")):
        share = prefix / "share" / "openbabel"
        for data in sorted(share.glob("*"), reverse=True) if share.is_dir() else []:
            if _has_data_files(data):
                layouts.append((prefix / "bin", data))
    return layouts


def configure_environment(executable: str = "") -> dict[str, str]:
    """配置 OpenBabel 运行环境。必须在 import openbabel 之前调用。"""
    global _configured

    env: dict[str, str] = {}
    layouts: list[tuple[Path, Path]] = []

    if executable:
        exe = Path(executable)
        if IS_WINDOWS:
            layouts.append((exe.parent, exe.parent.parent / "share" / "openbabel"))
        else:
            layouts.append((exe.parent, exe.parent.parent / "share" / "openbabel"))

    layouts.extend(_bundle_layouts())
    layouts.extend(_conda_layouts())
    layouts.extend(_wheel_layouts())
    layouts.extend(_system_layouts())

    data_dir: Path | None = None
    lib_dir: Path | None = None
    for candidate_lib, candidate_data in layouts:
        if data_dir is None and _has_data_files(candidate_data):
            data_dir = candidate_data
        if lib_dir is None and candidate_lib.is_dir() and any(
            candidate_lib.glob("formats_*.obf")
        ):
            lib_dir = candidate_lib
        if data_dir and lib_dir:
            break

    if data_dir is not None:
        current = os.environ.get("BABEL_DATADIR", "")
        if not current or not _has_data_files(Path(current)):
            os.environ["BABEL_DATADIR"] = str(data_dir)
            env["BABEL_DATADIR"] = str(data_dir)
    if lib_dir is not None:
        current_lib = os.environ.get("BABEL_LIBDIR", "")
        if not current_lib or not any(Path(current_lib).glob("formats_*.obf")):
            os.environ["BABEL_LIBDIR"] = str(lib_dir)
            env["BABEL_LIBDIR"] = str(lib_dir)

    if IS_WINDOWS:
        dll_dirs: list[Path] = []
        for candidate_lib, _ in layouts:
            if candidate_lib.is_dir() and any(candidate_lib.glob("openbabel-*.dll")):
                dll_dirs.append(candidate_lib)
        if data_dir is not None:
            dll_dirs.append(data_dir)
        for dll_dir in dll_dirs:
            try:
                os.add_dll_directory(str(dll_dir))
            except (AttributeError, OSError):
                pass
            env["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
            os.environ["PATH"] = env["PATH"]

    _configured = True
    return env


def _import_openbabel() -> Any | None:
    configure_environment()
    try:
        from openbabel import openbabel as ob  # type: ignore

        return ob
    except Exception as exc:  # 绑定缺失或 DLL 加载失败
        log.debug("OpenBabel Python 绑定不可用：%s", exc)
        return None


def find_obabel(executable: str = "") -> str:
    candidates = [executable] if executable else []
    for root in _bundle_roots():
        candidates.append(root / "bin" / ("obabel.exe" if IS_WINDOWS else "obabel"))
    candidates.extend(
        [
            Path(sys.prefix) / "Library" / "bin" / "obabel.exe" if IS_WINDOWS else None,
            Path(sys.prefix) / "bin" / "obabel",
            Path("/opt/homebrew/bin/obabel"),
            Path("/usr/local/bin/obabel"),
        ]
    )
    return find_executable(*candidates, names=("obabel",))


class OpenBabelAdapter:
    """对 OpenBabel 的薄封装，统一 Python API 与 CLI 两种后端。"""

    def __init__(self, executable: str = "") -> None:
        self.executable = find_obabel(executable)
        self._ob = None
        self._load_error = ""
        self._load()

    def _load(self) -> None:
        configure_environment(self.executable)
        ob = _import_openbabel()
        if ob is None:
            self._load_error = "OpenBabel Python 绑定不可用"
            return
        try:
            version = ob.OBReleaseVersion()
        except Exception:
            version = ""
        self._ob = ob
        self.version = version
        log.info("OpenBabel 就绪：Python API %s，CLI=%s", version, self.executable or "无")

    @property
    def has_python_api(self) -> bool:
        return self._ob is not None

    @property
    def available(self) -> bool:
        return self.has_python_api or bool(self.executable)

    @property
    def version_string(self) -> str:
        version = getattr(self, "version", "") or ""
        if version:
            return version
        if self.executable:
            result = run_process([self.executable, "-V"], timeout=15)
            return (result.output or "").strip().splitlines()[0] if result.output else ""
        return ""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _require(self) -> Any:
        if self._ob is None:
            raise DependencyError(
                "OpenBabel 不可用。请安装 openbabel-wheel（pip）或通过 conda 安装 openbabel。"
            )
        return self._ob

    def _cli(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        if not self.executable:
            return False, "未找到 obabel 命令行程序"
        result = run_process([self.executable, *args], cwd=cwd, timeout=300)
        if result.ok:
            return True, result.output
        return False, result.tail() or f"obabel 退出码 {result.returncode}"

    def convert(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        in_format: str = "",
        out_format: str = "",
        options: Iterable[str] = (),
        gasteiger: bool = False,
        rigid: bool = False,
        add_hydrogens: bool = False,
        ph: float = 7.4,
        gen3d: bool = False,
    ) -> tuple[bool, str]:
        """转换结构文件。返回 (成功, 消息)。"""
        input_path = Path(input_path)
        output_path = Path(output_path)
        in_format = in_format or input_path.suffix.lstrip(".").lower()
        out_format = out_format or output_path.suffix.lstrip(".").lower()
        if not input_path.is_file():
            return False, f"输入文件不存在：{input_path.name}"

        if self.has_python_api:
            ok, message = self._convert_api(
                input_path,
                output_path,
                in_format,
                out_format,
                list(options),
                gasteiger,
                rigid,
                add_hydrogens,
                ph,
                gen3d,
            )
            if ok:
                return True, message
            log.warning("OpenBabel Python 转换失败，尝试命令行：%s", message)
            if not self.executable:
                return False, message

        return self._convert_cli(
            input_path, output_path, in_format, out_format, list(options), gasteiger, rigid, gen3d
        )

    def _convert_api(
        self,
        input_path: Path,
        output_path: Path,
        in_format: str,
        out_format: str,
        options: list[str],
        gasteiger: bool,
        rigid: bool,
        add_hydrogens: bool,
        ph: float,
        gen3d: bool,
    ) -> tuple[bool, str]:
        ob = self._require()
        with _LOCK:
            try:
                conversion = ob.OBConversion()
                if not conversion.SetInAndOutFormats(in_format, out_format):
                    return False, f"OpenBabel 不支持 {in_format} → {out_format} 转换"
                mol = ob.OBMol()
                if not conversion.ReadFile(mol, str(input_path)):
                    return False, f"无法读取 {input_path.name}"
                if mol.NumAtoms() == 0:
                    return False, "读入的分子没有原子"

                if gen3d and mol.NumAtoms() > 0 and not _has_3d(mol):
                    builder = ob.OBBuilder()
                    builder.Build(mol)
                if add_hydrogens:
                    try:
                        mol.AddHydrogens(False, True, ph)
                    except Exception as exc:
                        log.debug("OpenBabel 加氢失败：%s", exc)
                if gasteiger:
                    try:
                        charge_model = ob.OBChargeModel.FindType("gasteiger")
                        if charge_model is not None:
                            charge_model.ComputeCharges(mol)
                        else:
                            log.warning("未找到 Gasteiger 电荷模型")
                    except Exception as exc:
                        log.warning("Gasteiger 电荷计算失败：%s", exc)
                for option in options:
                    conversion.AddOption(option, ob.OBConversion.OUTOPTIONS)
                if rigid:
                    conversion.AddOption("r", ob.OBConversion.OUTOPTIONS)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if not conversion.WriteFile(mol, str(output_path)):
                    return False, f"无法写出 {output_path.name}"
                if not output_path.is_file() or output_path.stat().st_size < 20:
                    return False, "OpenBabel 输出为空"
                return True, "OpenBabel Python API"
            except Exception as exc:
                return False, f"OpenBabel 转换异常：{exc}"

    def _convert_cli(
        self,
        input_path: Path,
        output_path: Path,
        in_format: str,
        out_format: str,
        options: list[str],
        gasteiger: bool,
        rigid: bool,
        gen3d: bool,
    ) -> tuple[bool, str]:
        if not self.executable:
            return False, "未找到 obabel 命令行程序"
        # 在 ASCII 目录中转换，避免中文路径导致 OpenBabel 打不开文件
        with work_directory("obabel") as tmp:
            local_in = tmp / f"input.{in_format}"
            local_out = tmp / f"output.{out_format}"
            shutil.copy2(input_path, local_in)
            args = [local_in.name, "-i" + in_format, "-o" + out_format, "-O", local_out.name]
            if rigid:
                args.append("-xr")
            if gasteiger:
                args.extend(["--partialcharge", "gasteiger"])
            if gen3d:
                args.append("--gen3d")
            for option in options:
                args.append(f"-x{option}" if len(option) == 1 else option)
            ok, message = self._cli(args, tmp)
            if not ok or not local_out.is_file() or local_out.stat().st_size < 20:
                return False, message or "obabel 未产生有效输出"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_out, output_path)
            return True, "OpenBabel CLI"

    # ------------------------------------------------------------------
    # 常用转换
    # ------------------------------------------------------------------
    def to_pdb(self, input_path: str | Path, output_path: str | Path) -> tuple[bool, str]:
        return self.convert(input_path, output_path, out_format="pdb")

    def receptor_to_pdbqt(self, pdb_path: str | Path, output_path: str | Path) -> tuple[bool, str]:
        return self.convert(
            pdb_path,
            output_path,
            out_format="pdbqt",
            gasteiger=True,
            rigid=True,
        )

    def ligand_to_pdbqt(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        add_hydrogens: bool = False,
        gen3d: bool = False,
    ) -> tuple[bool, str]:
        return self.convert(
            input_path,
            output_path,
            out_format="pdbqt",
            gasteiger=True,
            rigid=False,
            add_hydrogens=add_hydrogens,
            gen3d=gen3d,
        )

    def minimize(self, input_path: str | Path, output_path: str | Path, *, forcefield: str = "MMFF94s", steps: int = 250) -> tuple[bool, str]:
        """使用 OpenBabel 力场最小化（仅 Python API）。"""
        ob = self._require()
        with _LOCK:
            conversion = ob.OBConversion()
            conversion.SetInAndOutFormats(
                Path(input_path).suffix.lstrip(".").lower(),
                Path(output_path).suffix.lstrip(".").lower(),
            )
            mol = ob.OBMol()
            if not conversion.ReadFile(mol, str(input_path)):
                return False, "无法读取分子"
            field = ob.OBForceField.FindForceField(forcefield)
            if field is None or not field.Setup(mol):
                return False, f"力场 {forcefield} 不可用"
            field.ConjugateGradients(steps)
            field.GetCoordinates(mol)
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if not conversion.WriteFile(mol, str(output_path)):
                return False, "无法写出最小化结果"
        return True, f"OpenBabel {forcefield}"

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "python_api": self.has_python_api,
            "executable": self.executable,
            "version": self.version_string,
            "data_dir": os.environ.get("BABEL_DATADIR", ""),
            "library_dir": os.environ.get("BABEL_LIBDIR", ""),
            "error": self._load_error,
        }


def _has_3d(mol: Any) -> bool:
    """粗略判断分子是否具有非零 z 坐标。"""
    try:
        zs = [atom.GetZ() for atom in _iter_atoms(mol)]
        return any(abs(z) > 0.001 for z in zs)
    except Exception:
        return True


def _iter_atoms(mol: Any):
    for index in range(1, mol.NumAtoms() + 1):
        yield mol.GetAtom(index)


_default: OpenBabelAdapter | None = None


def get_openbabel(executable: str = "", refresh: bool = False) -> OpenBabelAdapter:
    global _default
    if _default is None or refresh or (executable and executable != _default.executable):
        _default = OpenBabelAdapter(executable)
    return _default
