load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_11674590.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：3.灰色粉晕染风格的蛋白-配体复合物，细节图
#  # =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================

remove solvent
set all_state, 1
bg_color white

# === 全局动漫风格光影设置 ===
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
set_color muted_amber, [0.85, 0.65, 0.25]  
set_color cloud_pink, [1.0, 0.85, 0.88]    
set_color pocket_cyan, [0.3, 0.7, 0.7] 

# === 对象定义 ===
select protein_sele, polymer.protein
select ligand_sele, organic

# ==================== 1. 主体全局渲染 (卡通骨架) ====================
hide everything, protein_sele
show cartoon, protein_sele
color grey60, ss h      
color grey75, ss s      
color grey85, ss l      

# ==================== 2. 小分子配体渲染 (保持球棍模型) ====================
hide everything, ligand_sele
show sticks, ligand_sele
show spheres, ligand_sele
set stick_radius, 0.12, ligand_sele
set sphere_scale, 0.28, ligand_sele 
set stick_quality, 100
set sphere_quality, 100

color muted_amber, elem C and ligand_sele
color skyblue, elem O and ligand_sele  
color lightblue, elem N and ligand_sele
color gold, elem S and ligand_sele
hide (elem H and ligand_sele)

# ==================== 3. 聚焦相互作用渲染 (改为纯棍状) ====================
select prot_polar, protein_sele and elem O+N+S
select lig_polar, ligand_sele and elem O+N+S
select hb_candidate_atoms, prot_polar within 3.5 of lig_polar
select hbond_residues, byres hb_candidate_atoms

# 【关键修正】：去掉了 show spheres，只显示 sticks，与小分子完美区分
show sticks, hbond_residues

color pocket_cyan, elem C and hbond_residues
color red, elem O and hbond_residues
color blue, elem N and hbond_residues
color gold, elem S and hbond_residues
hide (elem H and carbon) and hbond_residues

# === 绘制氢键虚线并显示距离 ===
distance hbonds, ligand_sele, protein_sele, 3.5, mode=2

# 【关键修正】：恢复显示距离标签，并独立设置其大小和颜色
show labels, hbonds
set label_color, grey30, hbonds
set label_size, 16, hbonds  # 距离数字略小，突出主次

set dash_color, grey30    
set dash_radius, 0.06      
set dash_gap, 0.3          

# === 添加残基标签 ===
label (hbond_residues and name CA), "%s %s" % (resn, resi)

# 为残基标签独立设置属性，防止影响距离标签
set label_color, black, hbond_residues
set label_font_id, 7, hbond_residues       
set label_size, 20, hbond_residues         
set label_position, (1.5, 1.5, 1.5), hbond_residues  

# ==================== 4. "云朵" 背景特效 (Surface) ====================
create protein_cloud, protein_sele
hide everything, protein_cloud
show surface, protein_cloud
set transparency, 0.85, protein_cloud     
set ray_transparency_contrast, 0          
set ray_transparency_shadows, 0           
color cloud_pink, protein_cloud

# === 自动放大聚焦 ===
zoom hbond_residues, buffer=2.0

print ">>> 样式脚本执行完毕，请用鼠标旋转至展示对接位点的最佳视角