#!/usr/bin/env python3
"""Standalone script to run ligand preprocessing directly."""
import sys
import os

# Ensure project root in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.path import init_app_root
init_app_root()

from src.core.ligand_processor import process_all_ligands

if __name__ == "__main__":
    print("=" * 60)
    print("DockFlowLab — 配体预处理")
    print("=" * 60)
    success, total, failed = process_all_ligands()
    print("\n" + "=" * 60)
    print(f"结果: 成功 {success}/{total}")
    if failed:
        print(f"失败: {len(failed)}")
        for f in failed:
            print(f"  - {f}")
    print("=" * 60)
