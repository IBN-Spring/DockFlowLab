load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_11674590.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：3.灰色粉晕染风格的蛋白-配体复合物，全览图
# =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================

remove solvent
set all_state, 1
bg_color white

# === 开启不透明背景，防止 Alpha 通道崩溃 ===
# 必须为 'on'，否则透明表面与黑线描边同时存在会崩溃
set ray_opaque_background, on   

# === 核心动漫光影设置 ===
set ray_trace_mode, 1          
set ray_trace_depth_factor, 1  
set ray_trace_disco_factor, 1  
set antialias, 2               
set ambient, 0.5               
set specular, off              
set ray_shadow, off            

# === 对象定义 ===
select protein_sele, polymer.protein
select ligand_sele, organic

# === 1. 蛋白主链渲染 (内部的卡通骨架) ===
hide everything, protein_sele
show cartoon, protein_sele
color grey60, ss h      # α螺旋为深灰
color grey75, ss s      # β折叠为中灰
color grey85, ss l      # Loop区为浅灰

select pocket, byres protein_sele within 5.0 of ligand_sele
color palecyan, pocket

# === 2. 小分子渲染  ===
hide everything, ligand_sele
show sticks, ligand_sele
show spheres, ligand_sele

# 优化球棍比例，使其更精致
set stick_radius, 0.12, ligand_sele
set sphere_scale, 0.28, ligand_sele 
set stick_quality, 100
set sphere_quality, 100

# === 定义并使用自定义暗琥珀色 ===
# 一种沉稳的介于橙黄之间的暗沉色调
set_color muted_amber, [0.85, 0.65, 0.25]
color muted_amber, elem C and ligand_sele

# 其他杂原子保持 CPK 标准色，便于识别化学结构
color skyblue, elem O and ligand_sele  # 柔和的水蓝色氧原子
color lightblue, elem N and ligand_sele # 柔和的浅蓝色氮原子
color gold, elem S and ligand_sele     # 金色硫原子
hide (elem H and ligand_sele)          # 隐藏氢原子保持画面整洁

# === 3. "云朵" 背景特效 (高透明 Surface 模型) ===
create protein_cloud, protein_sele
hide everything, protein_cloud
show surface, protein_cloud

# === 使用你完美的透明度方案 ===
set transparency, 0.85, protein_cloud     # 设置为高透明度(0.8-0.9范围)
set ray_transparency_contrast, 0          
set ray_transparency_shadows, 0           

set_color cloud_pink, [1.0, 0.85, 0.88]
color cloud_pink, protein_cloud

print ">>> 样式脚本执行完毕，请用鼠标旋转至展示对接位点的最佳视角