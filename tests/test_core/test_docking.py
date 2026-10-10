"""
测试 docking 模块
"""
import pytest
import os
from unittest.mock import Mock, patch, MagicMock

from src.core.docking import (
    validate_ligand_pdbqt,
    validate_pdb_conformation,
    run_single_docking,
    get_docking_engine,
)


class TestValidateLigandPDBQT:
    """测试配体PDBQT验证"""

    def test_valid_ligand_passes(self, tmp_path):
        """测试有效的配体通过验证"""
        pdbqt_file = tmp_path / "ligand.pdbqt"
        pdbqt_content = """REMARK  Name = aspirin
REMARK  3 active torsions:
ROOT
ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00    +0.000 C
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00    +0.000 C
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00    +0.000 C
ATOM      4  O   UNL     1      13.000  23.000  33.000  0.00  0.00    +0.000 O
ENDROOT
TORSDOF 3
"""
        pdbqt_file.write_text(pdbqt_content)

        result = validate_ligand_pdbqt(str(pdbqt_file))
        assert result is True

    def test_file_not_exists_fails(self):
        """测试文件不存在时验证失败"""
        result = validate_ligand_pdbqt("nonexistent.pdbqt")
        assert result is False

    def test_file_too_small_fails(self, tmp_path):
        """测试文件过小时验证失败"""
        pdbqt_file = tmp_path / "small.pdbqt"
        pdbqt_file.write_text("ATOM")  # 只有4字节

        result = validate_ligand_pdbqt(str(pdbqt_file))
        assert result is False

    def test_no_atoms_fails(self, tmp_path):
        """测试没有原子记录时验证失败"""
        pdbqt_file = tmp_path / "no_atoms.pdbqt"
        pdbqt_content = "REMARK This is a comment\n" * 20
        pdbqt_file.write_text(pdbqt_content)

        result = validate_ligand_pdbqt(str(pdbqt_file))
        assert result is False

    def test_too_few_atoms_fails(self, tmp_path):
        """测试原子数过少时验证失败"""
        pdbqt_file = tmp_path / "few_atoms.pdbqt"
        pdbqt_content = """REMARK  Name = test
ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00    +0.000 C
ATOM      2  O   UNL     1      11.000  21.000  31.000  0.00  0.00    +0.000 O
TORSDOF 0
"""
        pdbqt_file.write_text(pdbqt_content)

        result = validate_ligand_pdbqt(str(pdbqt_file))
        assert result is False  # 只有2个原子，少于3个

    def test_invalid_coordinates_fails(self, tmp_path):
        """测试坐标格式错误时验证失败"""
        pdbqt_file = tmp_path / "bad_coords.pdbqt"
        pdbqt_content = """REMARK  Name = test
ATOM      1  C   UNL     1      XXXX  20.000  30.000  0.00  0.00    +0.000 C
ATOM      2  C   UNL     1      11.000  YYYY  31.000  0.00  0.00    +0.000 C
ATOM      3  C   UNL     1      12.000  22.000  ZZZZ  0.00  0.00    +0.000 C
TORSDOF 0
"""
        pdbqt_file.write_text(pdbqt_content)

        result = validate_ligand_pdbqt(str(pdbqt_file))
        assert result is False

    def test_missing_torsdof_warning(self, tmp_path):
        """测试缺少TORSDOF时发出警告但通过验证"""
        pdbqt_file = tmp_path / "no_torsdof.pdbqt"
        pdbqt_content = """ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00    +0.000 C
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00    +0.000 C
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00    +0.000 C
"""
        pdbqt_file.write_text(pdbqt_content)

        log_output = []
        result = validate_ligand_pdbqt(str(pdbqt_file), log_text=log_output)

        # 应该通过验证（刚性分子可以没有TORSDOF）
        assert result is True
        # 但应该有警告日志
        assert any("TORSDOF" in str(msg) for msg in log_output)


class TestValidatePDBConformation:
    """测试PDB构象验证"""

    def test_identical_coordinates_perfect(self, tmp_path):
        """测试完全相同的坐标通过验证"""
        pdbqt_file = tmp_path / "original.pdbqt"
        pdbqt_content = """MODEL 1
ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00
ENDMDL
"""
        pdbqt_file.write_text(pdbqt_content)

        pdb_file = tmp_path / "converted.pdb"
        pdb_content = """HETATM    1  C   L       1      10.000  20.000  30.000  1.00  0.00
HETATM    2  C   L       1      11.000  21.000  31.000  1.00  0.00
HETATM    3  C   L       1      12.000  22.000  32.000  1.00  0.00
TER
"""
        pdb_file.write_text(pdb_content)

        result = validate_pdb_conformation(str(pdbqt_file), str(pdb_file))
        assert result is True

    def test_small_deviation_acceptable(self, tmp_path):
        """测试小偏差可接受"""
        pdbqt_file = tmp_path / "original.pdbqt"
        pdbqt_content = """ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00
"""
        pdbqt_file.write_text(pdbqt_content)

        pdb_file = tmp_path / "converted.pdb"
        # 微小偏差 (0.05Å)
        pdb_content = """HETATM    1  C   L       1      10.050  20.050  30.050  1.00  0.00
HETATM    2  C   L       1      11.050  21.050  31.050  1.00  0.00
HETATM    3  C   L       1      12.050  22.050  32.050  1.00  0.00
TER
"""
        pdb_file.write_text(pdb_content)

        result = validate_pdb_conformation(str(pdbqt_file), str(pdb_file))
        assert result is True

    def test_large_deviation_fails(self, tmp_path):
        """测试大偏差失败"""
        pdbqt_file = tmp_path / "original.pdbqt"
        pdbqt_content = """ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00
"""
        pdbqt_file.write_text(pdbqt_content)

        pdb_file = tmp_path / "converted.pdb"
        # 大偏差 (1.0Å)
        pdb_content = """HETATM    1  C   L       1      11.000  21.000  31.000  1.00  0.00
HETATM    2  C   L       1      12.000  22.000  32.000  1.00  0.00
HETATM    3  C   L       1      13.000  23.000  33.000  1.00  0.00
TER
"""
        pdb_file.write_text(pdb_content)

        result = validate_pdb_conformation(str(pdbqt_file), str(pdb_file))
        assert result is False

    def test_atom_count_tolerance(self, tmp_path):
        """测试原子数容差"""
        pdbqt_file = tmp_path / "original.pdbqt"
        pdbqt_content = """ATOM      1  C   UNL     1      10.000  20.000  30.000  0.00  0.00
ATOM      2  C   UNL     1      11.000  21.000  31.000  0.00  0.00
ATOM      3  C   UNL     1      12.000  22.000  32.000  0.00  0.00
ATOM      4  H   UNL     1      10.500  20.500  30.500  0.00  0.00
"""
        pdbqt_file.write_text(pdbqt_content)

        pdb_file = tmp_path / "converted.pdb"
        # 少了1个氢原子（在容差范围内）
        pdb_content = """HETATM    1  C   L       1      10.000  20.000  30.000  1.00  0.00
HETATM    2  C   L       1      11.000  21.000  31.000  1.00  0.00
HETATM    3  C   L       1      12.000  22.000  32.000  1.00  0.00
TER
"""
        pdb_file.write_text(pdb_content)

        result = validate_pdb_conformation(str(pdbqt_file), str(pdb_file))
        # 应该通过（差异在±3原子容差内）
        assert result is True


class TestGetDockingEngine:
    """测试对接引擎获取"""

    @patch('src.core.docking.CONFIG')
    def test_default_vina_cpu(self, mock_config):
        """测试默认使用Vina CPU版本"""
        mock_config.get.side_effect = lambda key, default=None: {
            'vina_path': 'vina.exe',
            'docking_engine': None,
        }.get(key, default)

        engine_path, engine_info = get_docking_engine()

        assert 'vina' in engine_path.lower()
        assert engine_info['name'] == 'Vina'

    @patch('src.core.docking.CONFIG')
    def test_vina_gpu_selection(self, mock_config):
        """测试选择Vina-GPU"""
        mock_config.get.side_effect = lambda key, default=None: {
            'vina_path': 'vina.exe',
            'docking_engine': 'Vina-GPU',
        }.get(key, default)

        engine_path, engine_info = get_docking_engine()

        assert 'gpu' in engine_path.lower() or 'GPU' in engine_info['name']


class TestRunSingleDocking:
    """测试单个对接任务"""

    @patch('src.core.docking.run_command')
    @patch('src.core.docking.get_docking_engine')
    def test_successful_docking(self, mock_engine, mock_run, tmp_path):
        """测试成功的对接"""
        # Mock引擎
        mock_engine.return_value = ('vina.exe', {'name': 'Vina', 'type': 'cpu'})

        # Mock命令执行
        mock_run.return_value = ("Vina output", True)

        # 创建测试文件
        receptor = tmp_path / "receptor.pdbqt"
        receptor.write_text("ATOM 1 CA")

        ligand = tmp_path / "ligand.pdbqt"
        ligand.write_text("ATOM 1 C")

        output = tmp_path / "output.pdbqt"
        config = tmp_path / "config.txt"
        config.write_text(f"""receptor = {receptor}
ligand = {ligand}
out = {output}
center_x = 10
center_y = 20
center_z = 30
size_x = 25
size_y = 25
size_z = 25
""")

        with patch('src.core.docking.CONFIG', {
            '3.受体': str(tmp_path),
            '4.配体': str(tmp_path),
            '6.对接结果': str(tmp_path),
        }):
            success, msg = run_single_docking(
                str(receptor),
                str(ligand),
                str(config),
            )

        assert success is True


@pytest.mark.integration
class TestDockingIntegration:
    """对接集成测试"""

    def test_full_docking_pipeline(self):
        """测试完整对接流程"""
        pytest.skip("需要真实的Vina可执行文件")


@pytest.mark.slow
class TestDockingPerformance:
    """对接性能测试"""

    def test_concurrent_docking_performance(self):
        """测试并发对接性能"""
        pytest.skip("性能测试，仅在需要时运行")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
