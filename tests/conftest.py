"""
DockFlowLab 测试配置
"""
import os
import sys
import pytest
from pathlib import Path

# 添加src到Python路径
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

# 测试数据目录
TEST_DATA_DIR = Path(__file__).parent / "test_data"
TEST_RECEPTORS_DIR = TEST_DATA_DIR / "receptors"
TEST_LIGANDS_DIR = TEST_DATA_DIR / "ligands"
TEST_OUTPUT_DIR = TEST_DATA_DIR / "outputs"


@pytest.fixture(scope="session")
def test_data_dir():
    """测试数据目录"""
    return TEST_DATA_DIR


@pytest.fixture(scope="session")
def test_receptors_dir():
    """测试受体目录"""
    return TEST_RECEPTORS_DIR


@pytest.fixture(scope="session")
def test_ligands_dir():
    """测试配体目录"""
    return TEST_LIGANDS_DIR


@pytest.fixture(scope="session")
def test_output_dir():
    """测试输出目录"""
    # 创建临时输出目录
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_OUTPUT_DIR
    # 测试结束后可选择清理
    # shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)


@pytest.fixture
def sample_receptor_pdb(test_receptors_dir):
    """示例受体PDB文件"""
    pdb_file = test_receptors_dir / "1HVR.pdb"
    if not pdb_file.exists():
        pytest.skip("示例受体文件不存在")
    return str(pdb_file)


@pytest.fixture
def sample_ligand_sdf(test_ligands_dir):
    """示例配体SDF文件"""
    sdf_file = test_ligands_dir / "aspirin.sdf"
    if not sdf_file.exists():
        pytest.skip("示例配体文件不存在")
    return str(sdf_file)


@pytest.fixture
def mock_config():
    """模拟配置"""
    return {
        "box_padding": 8.0,
        "box_size_range": [20.0, 100.0],
        "box_coverage": 1.0,
        "exhaustiveness": 8,
        "num_modes": 10,
        "energy_range": 3,
    }


@pytest.fixture
def mock_active_residues():
    """模拟活性残基"""
    return [
        {"id": "HIS41", "x": 10.0, "y": 20.0, "z": 30.0},
        {"id": "CYS145", "x": 12.0, "y": 22.0, "z": 32.0},
        {"id": "GLU166", "x": 11.0, "y": 21.0, "z": 31.0},
        {"id": "ASP187", "x": 13.0, "y": 23.0, "z": 33.0},
    ]


# pytest配置
def pytest_configure(config):
    """pytest启动配置"""
    config.addinivalue_line(
        "markers", "slow: 标记为慢速测试"
    )
    config.addinivalue_line(
        "markers", "integration: 标记为集成测试"
    )
    config.addinivalue_line(
        "markers", "gpu: 需要GPU的测试"
    )
    config.addinivalue_line(
        "markers", "optional: 可选依赖的测试"
    )


def pytest_collection_modifyitems(config, items):
    """根据标记跳过测试"""
    skip_slow = pytest.mark.skip(reason="使用 -m slow 运行慢速测试")
    skip_gpu = pytest.mark.skip(reason="需要GPU支持")
    skip_optional = pytest.mark.skip(reason="缺少可选依赖")

    for item in items:
        if "slow" in item.keywords and not config.getoption("-m") == "slow":
            item.add_marker(skip_slow)

        if "gpu" in item.keywords:
            # 检查是否有GPU
            try:
                import subprocess
                result = subprocess.run(["nvidia-smi"], capture_output=True)
                if result.returncode != 0:
                    item.add_marker(skip_gpu)
            except FileNotFoundError:
                item.add_marker(skip_gpu)

        if "optional" in item.keywords:
            # 检查可选依赖
            if "rdkit" in str(item.fspath):
                try:
                    import rdkit
                except ImportError:
                    item.add_marker(skip_optional)
