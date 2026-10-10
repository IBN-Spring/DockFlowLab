load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_5280343.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：2.流光珍珠风格的蛋白-配体复合物，细节图
#  原创：PANGOLINNN  欢迎关注小红书、bilibili账号：PANGOLINNN，获取更多顶刊一键配色脚本和相关科研教程
# =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================
remove solvent
set all_state, 1
bg_color white

# === 1. 写实光影设置 ===
set ray_opaque_background, on
set ray_trace_mode, 0        
set ray_shadows, on          
set depth_cue, on            
set fog_start, 0.45          

# 多光源与材质调优
set light_count, 8           
set ambient, 0.25            
set direct, 0.65             
set specular, 0.5            
set shininess, 45            
set antialias, 2
set orthoscopic, on          

# === 2. 美学配色库 ===
set_color pearl_grey, [0.85, 0.86, 0.88]   
set_color neon_magenta, [0.95, 0.15, 0.55] 
set_color cloud_pink, [1.0, 0.85, 0.88]    
set_color muted_amber_grey, [0.60, 0.50, 0.35]

select protein_sele, polymer.protein
select ligand_sele, organic

# ==================== 1. 主体全局渲染 (珍珠银灰卡通) ====================
hide everything, protein_sele
show cartoon, protein_sele
color pearl_grey, protein_sele

set cartoon_smooth_loops, 1 
set cartoon_fancy_helices, 1       
set cartoon_flat_sheets, 0         
set cartoon_side_chain_helper, 1

# ==================== 2. 小分子配体渲染 (精致宝石球棍) ====================
hide everything, ligand_sele
show sticks, ligand_sele
show spheres, ligand_sele
set stick_radius, 0.15, ligand_sele
set sphere_scale, 0.26, ligand_sele 
set stick_quality, 100
set sphere_quality, 100

color neon_magenta, elem C and ligand_sele
color white, elem O and ligand_sele      
color deepblue, elem N and ligand_sele
hide (elem H and ligand_sele)

# ==================== 3. 聚焦相互作用渲染 (绝对聚焦逻辑) ====================
select prot_polar, protein_sele and elem O+N+S
select lig_polar, ligand_sele and elem O+N+S
select hb_candidate_atoms, prot_polar within 3.6 of lig_polar
select hbond_residues, byres hb_candidate_atoms

show sticks, hbond_residues
set stick_radius, 0.1, hbond_residues 

color muted_amber_grey, elem C and hbond_residues
color red, elem O and hbond_residues
color blue, elem N and hbond_residues
color gold, elem S and hbond_residues
hide (elem H and carbon) and hbond_residues

# ==================== 4. 氢键虚线与距离标签  ====================
distance hbonds, ligand_sele, protein_sele, 3.5, mode=2

# 发光青色(cyan)粗虚线
set dash_color, cyan    
set dash_radius, 0.12      
set dash_gap, 0.25         

show labels, hbonds
set label_color, grey40, hbonds
set label_size, 18, hbonds  

# ==================== 5. 氨基酸主标签 (防遮挡与放大) ====================
label (hbond_residues and name CA), "%s %s" % (resn, resi)
set label_color, black, hbond_residues
set label_font_id, 7, hbond_residues       
set label_size, 30, hbond_residues         

# 牵引线防遮挡
set label_connector, on
set label_connector_color, grey50
set label_connector_width, 2

# ==================== 6. "云朵" 背景特效 (高透明 Surface) ====================
create protein_cloud, protein_sele
hide everything, protein_cloud
show surface, protein_cloud
set transparency, 0.9, protein_cloud     
set ray_transparency_contrast, 1.5           
set ray_transparency_shadows, 0           
color cloud_pink, protein_cloud

# === 自动缩小视角聚焦 ===
zoom hbond_residues, buffer=2.0

print ">>> 样式脚本执行完毕，欢迎关注PANGOLINNN，获取更多脚本和相关教程。请用鼠标旋转至展示对接位点的最佳视角，也可在命令行输入 'ray'进行高清渲染。"