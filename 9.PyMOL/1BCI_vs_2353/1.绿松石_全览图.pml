load C:/Users/39295/PycharmProjects/DockFlowLab/8.转换PDB/1BCI_vs_2353.pdb, complex
# =========================================================================
#  PyMOL 渲染脚本：1.绿松石风格的蛋白-配体复合物，全览图
#  原创：PANGOLINNN  欢迎关注小红书账号PANGOLINNN，获取更多顶刊一键配色脚本和相关科研教程
# =========================================================================
#  此脚本旨在优化分子对接结果的插图质量
# =========================================================================

# ================= 预处理 ===================
# 移除水分子和其他溶剂，保持画面整洁。
# 如果你的对接结果已经去水，这行代码不会有任何影响。
remove solvent
set all_state, 1  # 如果存在多个状态（例如对接结果中的多个构象），则全部处理

# ================= 全局视觉设置 =================
# 这些设置用于打造整体的高质量、插图般的视觉质感。
bg_color white
set orthoscopic, on    # 开启正交投影，提高清晰度，避免近大远小的透视变形
set specular, off      # 关闭生硬的高光，营造哑光、类似学术插画的质感
set ambient, 0.5       # 增加环境光，使打光更柔和、更均匀
set direct, 0.7        # 设置直射光强度
set antialias, 2       # 开启高质量抗锯齿，使边缘平滑
set line_width, 1.5    # 设置卡通模型和棍状模型的轮廓线宽度
set ray_shadows, on    # 开启柔和阴影，增加口袋的空间深度感
set ray_trace_color, black # 明确将轮廓描边颜色设置为黑色

# ================= 自定义颜色 =================
# 这些颜色经过微调，呈现出柔和、高级的视觉效果。
# Nature 风格绿松石色，用于蛋白表面（RGB 约 [0.15, 0.55, 0.55]）
set_color nature_teal, [0.15, 0.55, 0.55] 
# 柔和的粉色，用于突出显示配体的小分子碳骨架
set_color cpk_pink, [1.00, 0.75, 0.80]

# =========================================================================
#  对象选择 (OBJECT SELECTION) - **重要：用户必须检查此部分**
# =========================================================================
# 脚本需要知道什么是“受体蛋白”什么是“配体小分子”。
# 默认假设：蛋白是 'polymer.protein'，小分子是 'organic'。

# 默认选择逻辑：
select protein_sele, polymer.protein
select ligand_sele, organic

# *** 注意：如果 'organic' 无法准确捕捉你的小分子，请取消注释并编辑下面这行： ***
# select ligand_sele, resn YOUR_LIGAND_NAME_HERE  # 将 YOUR_LIGAND_NAME_HERE 替换为你对接小分子的 3 字母残基名称

# 安全检查：确保选择区内确实有原子
if (count_atoms("protein_sele") == 0) or (count_atoms("ligand_sele") == 0):
    print ">>> 警告：未找到 'protein_sele' 或 'ligand_sele'，请检查脚本中的对象选择部分。"
else:
    print ">>> 成功选中蛋白与配体，准备开始应用渲染样式。"

# ================= 蛋白渲染 =================
# 此部分用于生成绿松石色的受体蛋白表面，呈现高级的哑光质感。
hide everything, protein_sele
show surface, protein_sele
set transparency, 0, protein_sele # 确保表面是不透明的，防止杂乱
color nature_teal, protein_sele

# ================= 配体渲染 =================
# 此部分为对接好的配体应用粉色配色，并调整粗细，确保其在口袋中清晰醒目。
hide everything, ligand_sele
show sticks, ligand_sele
show spheres, ligand_sele

# 微调棍状和球状模型的参数，使其更符合发文标准的美观度
set stick_radius, 0.15, ligand_sele
set sphere_scale, 0.25, ligand_sele
set stick_quality, 100
set sphere_quality, 100

# 为配体原子上色：碳原子为粉色，其他杂原子采用柔和的传统配色
color cpk_pink, elem C and ligand_sele
color skyblue, elem O and ligand_sele  # 柔和的浅红色（氧原子）
color lightblue, elem N and ligand_sele # 柔和的浅蓝色（氮原子）
color gold, elem S and ligand_sele     # 金黄色（硫原子）

# ================= 最终光线追踪渲染设置 =================
# 以下命令用于添加标志性的黑色轮廓描边。
set ray_trace_mode, 1  # 开启模式1，呈现带有黑色描边的学术插画风格

# 可选高级操作：调整 'disco_factor' 和 'gain' 来微调阴影和光照。
set ray_trace_disco_factor, 0.7  # 控制阴影边缘的软硬度
set ray_trace_gain, 0.1         # 控制高光和阴影的融合程度

print ">>> 样式脚本执行完毕，欢迎关注PANGOLINNN，获取更多脚本和相关教程。请用鼠标旋转至展示对接位点的最佳视角，也可在命令行输入 'ray'进行高清渲染。"