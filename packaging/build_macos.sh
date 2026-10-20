#!/usr/bin/env bash
# 在 macOS 上构建 DockFlowLab.app。
# 依赖：Python 3.10+，pip install -e ".[chem,build]"
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
ARCH="$(uname -m)"
mkdir -p release

if ! "$PYTHON" -c "import PyInstaller, PySide6, rdkit, meeko, openmm, pdbfixer" 2>/dev/null; then
  echo "[错误] 依赖不完整，请先执行： $PYTHON -m pip install -e '.[chem,build]'" >&2
  exit 1
fi

# 构建机已安装 Vina 时自动收进应用包；也可以通过 DOCKFLOWLAB_VINA 指定
if [[ -z "${DOCKFLOWLAB_VINA:-}" ]] && command -v vina >/dev/null 2>&1; then
  export DOCKFLOWLAB_VINA="$(command -v vina)"
  echo "[提示] 将打包 Vina：${DOCKFLOWLAB_VINA}"
fi

echo "[1/3] PyInstaller 构建..."
"$PYTHON" -m PyInstaller --clean --noconfirm packaging/dockflowlab.spec

echo "[2/3] 压缩应用包..."
if [[ -d dist/DockFlowLab.app ]]; then
  ditto -c -k --sequesterRsrc --keepParent \
    "dist/DockFlowLab.app" "release/DockFlowLab-macOS-${ARCH}.zip"
else
  (cd dist && zip -qry "../release/DockFlowLab-macOS-${ARCH}.zip" DockFlowLab)
fi

echo "[3/3] 完成：release/DockFlowLab-macOS-${ARCH}.zip"
echo "提示：首次打开未签名应用时，可右键 →“打开”，或在“系统设置 → 隐私与安全性”中允许。"
