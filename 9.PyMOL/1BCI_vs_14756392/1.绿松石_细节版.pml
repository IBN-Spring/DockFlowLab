load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_14756392.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：1.绿松石风格的蛋白-配体复合物，细节图
#  原创：PANGOLINNN  欢迎关注小红书账号PANGOLINNN，获取更多顶刊一键配色脚本和相关科研教程
# =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================
# === 初始化与全局设置 ===
remove solvent
set all_state, 1
bg_color white
set orthoscopic, on
set specular, off
set ambient, 0.5
set direct, 0.7
set antialias, 2
set ray_shadows, off       # 放大图中关闭阴影，避免遮挡细节
set ray_trace_mode, 1      # 保持经典的黑线描边
set ray_trace_color, black
# === 定义配色 ===
set_color nature_teal, [0.15, 0.55, 0.55] 
set_color cpk_pink, [1.00, 0.75, 0.80]
# === 定义对象（请根据需要修改 ligand_sele） ===
select protein_sele, polymer.protein
select ligand_sele, organic 
# select ligand_sele, resn YOUR_LIGAND_NAME_HERE  # 如果 organic 选不到，用这行
# === 蛋白全局渲染 (Cartoon) ===
hide everything, protein_sele
show cartoon, protein_sele
color nature_teal, protein_sele
set cartoon_flat_sheets, 0  # 让β折叠(箭头)看起来更立体饱满
# === 小分子渲染 (Sticks) ===
hide everything, ligand_sele
show sticks, ligand_sele
set stick_radius, 0.15, ligand_sele
color cpk_pink, elem C and ligand_sele
color skyblue, elem O and ligand_sele
color lightblue, elem N and ligand_sele
color gold, elem S and ligand_sele
# === 结合口袋关键残基提取与渲染 ===
# 只挑选蛋白与小分子之间存在极性接触（杂原子距离在 3.5 埃以内）的核心关键残基。
select pocket, byres ( (protein_sele and not elem C,H) within 3.5 of (ligand_sele and not elem C,H) )
# 显示这些口袋氨基酸的侧链 (Sticks)
show sticks, pocket
# 将口袋氨基酸的碳原子也染成绿松石色，保持整体色调统一，杂原子保持标准色
color nature_teal, elem C and pocket
color skyblue, elem O and pocket
color lightblue, elem N and pocket
color gold, elem S and pocket
# 隐藏非极性氢原子，让画面更干净
hide (elem H and carbon)
# === 氢键计算与渲染 ===
# 计算小分子和口袋之间的氢键 (mode=2 专门用于寻找氢键)，距离阈值设为 3.5 埃
distance hbonds, ligand_sele, pocket, 3.5, mode=2
# 格式化氢键虚线样式
hide labels, hbonds        # 隐藏默认的距离数字标签（例如"2.8"）
set dash_color, grey50     # 虚线颜色设为深灰色
set dash_radius, 0.06      # 虚线粗细
set dash_gap, 0.3          # 虚线间距
# === 氨基酸残基标签 ===
# 在口袋氨基酸的 alpha 碳 (CA) 上显示残基名称和编号 (例如: TYR 123)
label pocket and name CA, "%s %s" % (resn, resi)
# 标签字体美化设置
set label_color, black
set label_font_id, 7       # 7 是无衬线粗体，非常适合学术作图
set label_size, 18         # 字体大小
set label_position, (1.5, 1.5, 1.5)  # 让标签稍微偏移原子中心，避免重叠
print ">>> 样式脚本执行完毕，欢迎关注PANGOLINNN，获取更多脚本和相关教程。请用鼠标旋转至展示对接位点的最佳视角，也可在命令行输入 'ray'进行高清渲染。"