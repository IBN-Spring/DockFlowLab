load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_2353.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：2.流光珍珠风格的蛋白-配体复合物，全览图
#  原创：PANGOLINNN  欢迎关注小红书、bilibili账号：PANGOLINNN，获取更多顶刊一键配色脚本和相关科研教程
# =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================

remove solvent
set all_state, 1
bg_color white

# === 1. 电影级真实光影系统 (打破2D扁平，拥抱3D) ===
# 开启安全阀，防止透明度崩溃
set ray_opaque_background, on

set ray_trace_mode, 0        # 关闭黑线描边
set ray_shadows, on          # 开启真实阴影，制造空间纵深感
set depth_cue, on            # 开启景深 (随着距离变远，结构会略微融入背景)
set fog_start, 0.45          

# 多光源与材质调优
set light_count, 8           # 增加光源数量，让受光面更细腻
set ambient, 0.25            # 压低全局环境光
set direct, 0.65             # 提高主光源直射强度
set specular, 0.5            # 开启高光反光
set shininess, 45            # 调整高光锐度，呈现类似磨砂玻璃/珍珠的光泽
set antialias, 2

# === 2. 美学配色库 ===
set_color pearl_grey, [0.85, 0.86, 0.88]   # 珍珠银白
set_color aura_blue, [0.65, 0.85, 0.95]    # 极光水蓝
set_color neon_magenta, [0.95, 0.15, 0.55] # 霓虹洋红

select protein_sele, polymer.protein
select ligand_sele, organic

# === 3. 蛋白主体：流线型珍珠艺术雕塑 ===
hide everything, protein_sele
show cartoon, protein_sele
color pearl_grey, protein_sele

# 开启高级平滑算法，让卡通结构极其柔美丝滑
set cartoon_smooth_loops, 1 
set cartoon_fancy_helices, 1       
set cartoon_flat_sheets, 0         

# === 4. 视觉焦点：如发光宝石般的配体 ===
hide everything, ligand_sele
show sticks, ligand_sele
show spheres, ligand_sele
set stick_radius, 0.15, ligand_sele
set sphere_scale, 0.32, ligand_sele 
set stick_quality, 100
set sphere_quality, 100

# 高对比度的色彩锁定目光
color neon_magenta, elem C and ligand_sele
color white, elem O and ligand_sele      # 氧原子用纯白提亮，像高光点缀
color deepblue, elem N and ligand_sele
hide (elem H and ligand_sele)

# === 5. 点缀：极光水膜云朵 (Surface) ===
create protein_cloud, protein_sele
hide everything, protein_cloud
show surface, protein_cloud

color aura_blue, protein_cloud
set transparency, 0.82, protein_cloud

# 参数：制造玻璃边缘的折射反光感
set ray_transparency_contrast, 1.5   
set ray_transparency_shadows, 0      # 保持内部通透，不投射的阴影

# 自动寻找最佳宏观视角
zoom all, buffer=2.5
orient all

print ">>> 样式脚本执行完毕，欢迎关注PANGOLINNN，获取更多脚本和相关教程。请用鼠标旋转至展示对接位点的最佳视角，也可在命令行输入 'ray'进行高清渲染。"