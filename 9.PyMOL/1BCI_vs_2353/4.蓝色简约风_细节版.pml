load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_2353.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：4.蓝色简约风格的蛋白-配体复合物，细节图
# =========================================================================
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

# === 定义高级配色 ===
# 蛋白质：干净的哑光蓝色 
set_color ref_blue, [0.35, 0.70, 0.90] # 干净、哑光的蓝色

# 配体：橙黄色
set_color ref_amber, [0.95, 0.75, 0.10] # Brighter than muted_amber, closer to image

# === 对象定义 ===
select protein_sele, polymer.protein
select ligand_sele, organic

# ==================== 1. 主体全局渲染 ====================
hide everything, protein_sele
show cartoon, protein_sele


color ref_blue, protein_sele
set cartoon_sampling, 14
set cartoon_backbone_as_tube, 1
set cartoon_radius, 0.25, protein_sele # 增加螺旋半径，使其更饱满
set cartoon_flat_sheets, 0
set cartoon_side_chain_helper, 1

# ==================== 2. 小分子配体渲染====================
hide everything, ligand_sele
show sticks, ligand_sele
set stick_radius, 0.2, ligand_sele
set stick_quality, 100


color ref_amber, elem C and ligand_sele
color skyblue, elem O and ligand_sele  # 标准柔和 CPK 氧原子
color lightblue, elem N and ligand_sele # 柔和浅蓝氮原子
color gold, elem S and ligand_sele     # 金色硫原子
hide (elem H and ligand_sele)

# ==================== 3. 聚焦相互作用渲染 ====================
select prot_polar, protein_sele and elem O+N+S
select lig_polar, ligand_sele and elem O+N+S
select hb_candidate_atoms, prot_polar within 3.5 of lig_polar
select hbond_residues, byres hb_candidate_atoms

# 显示关键残基球棍模型
show sticks, hbond_residues
set stick_radius, 0.12, hbond_residues # 侧链略细一些，与配体区分


color ref_blue, elem C and hbond_residues
color red, elem O and hbond_residues
color blue, elem N and hbond_residues
color gold, elem S and hbond_residues
hide (elem H and carbon) and hbond_residues

# === 绘制氢键虚线并显示距离 ===
distance hbonds, ligand_sele, protein_sele, 3.5, mode=2
show labels, hbonds
set label_color, grey30, hbonds
set label_size, 16, hbonds


set dash_color, magenta    
set dash_radius, 0.08      
set dash_gap, 0.2          

# === 添加标签设置 ===
label (hbond_residues and name CA), "%s %s" % (resn, resi)
set label_color, black, hbond_residues
set label_font_id, 7, hbond_residues       
set label_size, 20, hbond_residues         
set label_position, (1.5, 1.5, 1.5), hbond_residues  


# === 自动放大聚焦相互作用区 ===
zoom hbond_residues, buffer=2.0

print ">>> 样式脚本执行完毕，请用鼠标旋转至展示对接位点的最佳视角