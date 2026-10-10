load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_5373773.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：4.蓝色简约风格的蛋白-配体复合物，全览图
# # =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================

remove solvent
set all_state, 1
bg_color white

# === 全局光影设置  ===
set ray_opaque_background, on   
set ray_trace_mode, 1          
set ray_trace_depth_factor, 1  
set ray_trace_disco_factor, 1  
set antialias, 2               
set ambient, 0.5               
set specular, off              
set ray_shadow, off            
set orthoscopic, on   

# === 定义配色 ===
set_color muted_blue, [0.35, 0.70, 0.90] # 干净、哑光的蓝色

# 小分子：琥珀色
set_color muted_amber, [0.85, 0.65, 0.25] 

# === 对象定义 ===
select protein_sele, polymer.protein
select ligand_sele, organic

# ==================== 1. 主体全局渲染 (蓝色柱状卡通) ====================
hide everything, protein_sele
show cartoon, protein_sele


color muted_blue, protein_sele
set cartoon_ring_mode, 3, protein_sele
# 并结合 Backbone as Tube，增加卡通半径，使其看起来丰满有质感
set cartoon_backbone_as_tube, 1, protein_sele
set cartoon_radius, 0.25, protein_sele # 增加螺旋半径，使其更饱满

# ==================== 2. 小分子配体渲染 ====================
hide everything, ligand_sele
show sticks, ligand_sele
set stick_radius, 0.15, ligand_sele
set stick_quality, 100

# 原子使用 CPK 色调，碳原子使用琥珀色
color muted_amber, elem C and ligand_sele
color skyblue, elem O and ligand_sele  # 标准柔和 CPK 氧原子
color lightblue, elem N and ligand_sele # 柔和浅蓝氮原子
color gold, elem S and ligand_sele     # 金色硫原子
hide (elem H and ligand_sele)



# === 自动缩小视角 ===
zoom all, buffer=2.0
orient all

print ">>> 样式脚本执行完毕，请用鼠标旋转至展示对接位点的最佳视角