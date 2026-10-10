"""
测试 box_generator 模块
"""
import pytest
import numpy as np
from unittest.mock import Mock, patch, MagicMock

from src.core.box_generator import (
    calculate_initial_box,
    calculate_protein_center,
    auto_adjust_box,
    calculate_coverage,
    extract_active_residue_coords,
    extract_cocrystallized_ligand_coords,
    process_small_molecule_box,
)


class TestCalculateInitialBox:
    """测试初始盒子计算"""

    def test_ligand_box_smaller_than_residue_box(self):
        """测试配体盒子应小于残基盒子"""
        coords = np.array([
            [10.0, 20.0, 30.0],
            [15.0, 25.0, 35.0],
            [12.0, 22.0, 32.0],
        ])

        center_lig, size_lig = calculate_initial_box(coords, ligand_flag=True)
        center_res, size_res = calculate_initial_box(coords, ligand_flag=False)

        # 中心应该相同
        np.testing.assert_array_almost_equal(center_lig, center_res)

        # 残基盒子应该更大
        assert np.all(size_res >= size_lig), "残基盒子应大于或等于配体盒子"

    def test_empty_coords_returns_default(self):
        """测试空坐标返回默认值"""
        coords = np.array([])
        center, size = calculate_initial_box(coords, ligand_flag=True)

        assert center == (0.0, 0.0, 0.0)
        assert size == (25.0, 25.0, 25.0)

    def test_minimum_size_enforced(self):
        """测试最小尺寸限制"""
        # 非常小的分子
        coords = np.array([
            [10.0, 10.0, 10.0],
            [11.0, 11.0, 11.0],
        ])

        center, size = calculate_initial_box(coords, ligand_flag=True)

        # 尺寸应至少为25Å
        assert all(s >= 25.0 for s in size)


class TestCalculateProteinCenter:
    """测试蛋白质中心计算"""

    def test_fallback_to_numpy_when_openbabel_fails(self, tmp_path):
        """测试OpenBabel失败时回退到NumPy"""
        # 创建简单的PDB文件
        pdb_file = tmp_path / "test.pdb"
        pdb_content = """ATOM      1  CA  ALA A   1      10.000  20.000  30.000  1.00  0.00
ATOM      2  CA  GLY A   2      15.000  25.000  35.000  1.00  0.00
END
"""
        pdb_file.write_text(pdb_content)

        # Mock OpenBabel失败
        with patch('src.core.box_generator.ob', side_effect=ImportError):
            center = calculate_protein_center(str(pdb_file))

        # 应该使用NumPy回退方案
        expected_center = (12.5, 22.5, 32.5)
        np.testing.assert_array_almost_equal(center, expected_center, decimal=1)


class TestAutoAdjustBox:
    """测试盒子自动调整"""

    def test_no_active_residues_returns_safe_size(self):
        """测试无活性残基时返回安全尺寸"""
        center = np.array([10.0, 20.0, 30.0])
        size = np.array([5.0, 5.0, 5.0])  # 过小的尺寸

        adjusted_center, adjusted_size = auto_adjust_box(
            center, size, active_residues=[]
        )

        # 应返回至少20Å的安全尺寸
        assert all(s >= 20.0 for s in adjusted_size)

    def test_coverage_expansion(self, mock_active_residues):
        """测试覆盖率不足时扩展盒子"""
        center = np.array([11.0, 21.0, 31.0])
        size = np.array([2.0, 2.0, 2.0])  # 很小的盒子

        # Mock配置
        with patch('src.core.box_generator.CONFIG', {
            'box_coverage': 0.8,  # 目标80%覆盖率
            'box_size_range': [10.0, 100.0],
            'limit_volume': False,
        }):
            adjusted_center, adjusted_size = auto_adjust_box(
                center, size, mock_active_residues
            )

        # 盒子应该被扩展
        assert all(adjusted_size[i] > size[i] for i in range(3))

    def test_coverage_reduction(self, mock_active_residues):
        """测试覆盖率过高时缩小盒子"""
        center = np.array([11.0, 21.0, 31.0])
        size = np.array([100.0, 100.0, 100.0])  # 很大的盒子

        with patch('src.core.box_generator.CONFIG', {
            'box_coverage': 0.5,  # 目标50%覆盖率
            'box_size_range': [10.0, 100.0],
            'limit_volume': False,
        }):
            adjusted_center, adjusted_size = auto_adjust_box(
                center, size, mock_active_residues
            )

        # 盒子应该被缩小
        assert all(adjusted_size[i] < size[i] for i in range(3))

    def test_no_infinite_loop(self, mock_active_residues):
        """测试不会陷入死循环"""
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError("盒子调整陷入死循环")

        # 设置10秒超时（仅在Unix系统有效）
        try:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(10)

            center = np.array([11.0, 21.0, 31.0])
            size = np.array([30.0, 30.0, 30.0])

            with patch('src.core.box_generator.CONFIG', {
                'box_coverage': 0.75,
                'box_size_range': [10.0, 100.0],
                'limit_volume': False,
            }):
                adjusted_center, adjusted_size = auto_adjust_box(
                    center, size, mock_active_residues
                )

            signal.alarm(0)  # 取消超时
        except AttributeError:
            # Windows 不支持 signal.SIGALRM，跳过
            pytest.skip("信号超时检测仅支持Unix系统")


class TestCalculateCoverage:
    """测试覆盖率计算"""

    def test_full_coverage(self):
        """测试100%覆盖率"""
        center = (10.0, 10.0, 10.0)
        size = (20.0, 20.0, 20.0)

        residues = [
            {"x": 5.0, "y": 5.0, "z": 5.0},
            {"x": 15.0, "y": 15.0, "z": 15.0},
        ]

        coverage, count = calculate_coverage(center, size, residues)

        assert coverage == 100.0
        assert count == 2

    def test_partial_coverage(self):
        """测试部分覆盖率"""
        center = (10.0, 10.0, 10.0)
        size = (10.0, 10.0, 10.0)

        residues = [
            {"x": 10.0, "y": 10.0, "z": 10.0},  # 在盒子内
            {"x": 50.0, "y": 50.0, "z": 50.0},  # 在盒子外
        ]

        coverage, count = calculate_coverage(center, size, residues)

        assert coverage == 50.0
        assert count == 1

    def test_no_coverage(self):
        """测试0%覆盖率"""
        center = (10.0, 10.0, 10.0)
        size = (5.0, 5.0, 5.0)

        residues = [
            {"x": 50.0, "y": 50.0, "z": 50.0},
            {"x": 60.0, "y": 60.0, "z": 60.0},
        ]

        coverage, count = calculate_coverage(center, size, residues)

        assert coverage == 0.0
        assert count == 0


class TestExtractActiveResidues:
    """测试活性残基提取"""

    def test_extract_known_active_residues(self, tmp_path):
        """测试提取已知活性残基"""
        pdbqt_file = tmp_path / "receptor.pdbqt"
        pdbqt_content = """ATOM      1  CA  HIS A  41      10.000  20.000  30.000  1.00  0.00
ATOM      2  CA  CYS A 145      15.000  25.000  35.000  1.00  0.00
ATOM      3  CA  ALA A 100      12.000  22.000  32.000  1.00  0.00
END
"""
        pdbqt_file.write_text(pdbqt_content)

        residues = extract_active_residue_coords(str(pdbqt_file))

        # 应该提取到HIS和CYS（ALA不是活性残基）
        assert len(residues) == 2
        assert any(r["id"] == "HIS41" for r in residues)
        assert any(r["id"] == "CYS145" for r in residues)


class TestExtractCocrystallizedLigand:
    """测试共晶配体提取"""

    def test_extract_hetatm_coords(self, tmp_path):
        """测试提取HETATM坐标"""
        pdbqt_file = tmp_path / "receptor.pdbqt"
        pdbqt_content = """ATOM      1  CA  ALA A   1      10.000  20.000  30.000  1.00  0.00
HETATM    2  C1  LIG A 200      15.000  25.000  35.000  1.00  0.00
HETATM    3  C2  LIG A 200      16.000  26.000  36.000  1.00  0.00
END
"""
        pdbqt_file.write_text(pdbqt_content)

        coords = extract_cocrystallized_ligand_coords(str(pdbqt_file))

        assert coords is not None
        assert len(coords) == 2
        assert coords[0][0] == 15.0

    def test_skip_water_molecules(self, tmp_path):
        """测试跳过水分子"""
        pdbqt_file = tmp_path / "receptor.pdbqt"
        pdbqt_content = """HETATM    1  O   HOH A 200      15.000  25.000  35.000  1.00  0.00
HETATM    2  C1  LIG A 201      16.000  26.000  36.000  1.00  0.00
END
"""
        pdbqt_file.write_text(pdbqt_content)

        coords = extract_cocrystallized_ligand_coords(str(pdbqt_file))

        # 应该只提取非水分子
        assert coords is not None
        assert len(coords) == 1


class TestProcessSmallMoleculeBox:
    """测试小分子盒子处理"""

    def test_generates_appropriate_box(self, tmp_path):
        """测试生成合适的小分子盒子"""
        pdbqt_file = tmp_path / "small_mol.pdbqt"
        pdbqt_content = """ATOM      1  C1  MOL     1      10.000  20.000  30.000  1.00  0.00
ATOM      2  C2  MOL     1      15.000  25.000  35.000  1.00  0.00
END
"""
        pdbqt_file.write_text(pdbqt_content)

        with patch('src.core.box_generator.CONFIG', {
            'box_padding': 8.0,
            'limit_volume': False,
        }):
            center, size = process_small_molecule_box(str(pdbqt_file))

        # 中心应该在分子中心附近
        expected_center = (12.5, 22.5, 32.5)
        np.testing.assert_array_almost_equal(center, expected_center, decimal=0)

        # 尺寸应该合理（跨度 + padding）
        assert all(s >= 10.0 for s in size)


@pytest.mark.integration
class TestBoxGenerationIntegration:
    """盒子生成集成测试"""

    def test_full_box_generation_pipeline(self, sample_receptor_pdb, tmp_path):
        """测试完整的盒子生成流程"""
        # 这是一个集成测试，需要真实的受体文件
        pytest.skip("需要真实的受体文件进行集成测试")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
