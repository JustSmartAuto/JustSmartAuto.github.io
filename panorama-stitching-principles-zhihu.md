# 全景拼图算法的原理与技术细节

> **来源作者**：炼丹师（视觉算法工程师，知乎）
> **原文链接**：https://zhuanlan.zhihu.com/p/（原文页面已保存为本地 HTML，链接以存档为准）
> **发布时间**：2025-04-18
> **归档整理**：2026-11-19（由本地保存的网页 HTML 经 markitdown 转换并人工整理排版）

> **阅读说明**：原文的示意图与全部公式均为内联图片，markitdown 无法提取其内容，正文中以「〔公式图〕」「〔示意图〕」标注。公式编号（式 31–式 92）沿用原文，便于与 OpenCV stitching 源码对照。

---

## 全景拼图基本原理概述

### 共视平面

从 3D 场景到 2D 图像是通过射线投影得到的。如果把所有图像都投影到同一个平面上，显然就可以进行图像拼接。

**基本流程**：

1. 从同一位置拍摄一系列图像，即围绕光心旋转相机；
2. 计算第二幅图像与第一幅图像之间的变换；
3. 计算第二幅图像，使其与第一幅图像部分重合；
4. 将两者融合在一起，创建拼图；
5. 如果还有更多图像，重复以上步骤。

在图像配准时，使用最小二乘法求得单应矩阵。假设图像一和图像二的参数如原文示意〔示意图〕，则两幅图像之间的变换可分解为三步：

- **步骤 1**：将图像 2 中的像素转换为相机 2 坐标系中的射线〔公式图〕；
- **步骤 2**：将相机 2 坐标系中的射线转换为相机 1 坐标系中的射线〔公式图〕；
- **步骤 3**：将相机 1 坐标系中的射线转换为图像 1 坐标中的像素〔公式图〕。

通过上述三步，就可以把图像 1 融合到图像 2 的平面上，再配合相应的融合算法即可得到融合图。

### 全景

虽然投影到统一平面上十分简单，但无法实现全景图。如果想要 360 度视野，投影成球形再展开是更好的选择。

此时的成像面已不是平面而是球体，相机位于球体中心。保持光心不动、旋转相机，即可得到一个球面 360 度的图像；将球体展开，就形成了全景图。

球面投影同样是在做平面投影，只不过把 3D 物体投影到球面上。这个过程涉及三个坐标系：世界坐标系、相机坐标系（球面坐标系）、像面坐标系（球面图像坐标）。

首先将世界坐标系转换成相机坐标系，此时相机坐标系的原点在球的中心，相机坐标为三维坐标。为了表示为平面坐标，新设两个变量 θ、φ：

- θ 是射线与 YZ 平面的夹角，顺时针为正；
- φ 是射线与 XZ 平面的夹角，顺时针为负。

由此可将球体坐标转换为球面坐标〔公式图〕，之后将球体展开，两个坐标分别为 θ 和 φ〔示意图〕，最后把球面坐标转化为球面图像坐标。

由于上述球体是单位球，为了得到更好的图像表示需要做伸缩，通常使用半径为 S 的球体（S 为相机焦距）。与平面成像一样，使用相似定律并加上平移即可得到最终的图像坐标〔公式图〕。S 对图像的影响见原文示意〔示意图〕。

已知通过球面坐标形成全景图的原理后，只需对齐球面坐标。相机围绕垂直轴旋转 θ 角度得到的两幅图像〔示意图〕，对齐时水平平移 θ 角度即可——这意味着可以通过平移来对齐球面坐标。

### 装配全景图

理想情况下，只需将每一对图像拼接、融合，然后裁剪即可。

### 漂移

实际应用中，随时间的推移会积累微小误差，产生漂移（drift）现象。

解决办法有多种，最简单的是在末尾添加第一幅图像的另一个副本，从而得到约束 yₙ = y₁。之后可以通过以下方式处理：

- 增加位移 (y₁ − yₙ)/(n − 1) 到第一个之后的每幅图像，即整体向上平移；
- 应用仿射扰动：y′ = y + ax；
- 求解一个带此约束的大优化问题——效果最好，但更复杂。

### 融合

由于亮度差异等问题，图像融合往往并不完美。为了无缝融合需要专门算法，最简单的是羽化（feathering）。

### 羽化

在两幅图像的连接处，设置一个从 0 到 1 的渐变和一个从 1 到 0 的渐变，然后在渐变区间内将两者的像素值相加即可。

这种方法需要选择一个恰当的渐变区间，不同的渐变区间对融合效果影响不同。除此之外还有金字塔混合和 Alpha 混合。

---

## 算法运行流程方案

1. 命令行调用算法 SDK，输入源图像路径以及程序参数；
2. 特征点检测，判断使用 SURF 还是 ORB、SIFT、AKAZE，默认 SURF；
3. 对图像的特征点进行匹配，使用最近邻和次近邻方法，将两个最优匹配的置信度保存下来；
4. 对图像进行排序，并将置信度高的图像保存到同一个集合中，删除置信度较低的图像间匹配，得到能正确匹配的图像序列——即将置信度高于门限的所有匹配合并到一个集合；
5. 对所有图像进行相机参数粗略估计，然后求出旋转矩阵；
6. 使用光束平差法进一步精确估计旋转矩阵；
7. 波形校正，水平或垂直；
8. 拼接；
9. 融合：多频段融合、光照补偿。

---

## 图像特征点提取

### 原理

图像拼接的第一步是计算图像的特征并得到它们的描述符。好的特征检测算法有 SIFT 和 SURF，但两者受专利保护，使用上有一定限制。ORB 算法是一种很好的替代方法，它利用 FAST 检测特征，并生成 BRIEF 描述符。

### 部分代码

OpenCV 中应用的是 SURF 算法和 ORB 算法。`detail::FeaturesFinder` 表示寻找图像特征的类。该类重载的 `()` 运算符主要任务是调用 `find` 函数检测图像特征；`find` 是虚函数，具体执行的是两个子类 `SurfFeaturesFinder` 和 `OrbFeaturesFinder` 中的 `find` 函数，分别对应 SURF 特征和 ORB 特征。

ORB 特征点的构造函数内部包含参数设置，提取特征点的 `find` 函数本文不展开。

---

## 图像特征点匹配与单应矩阵的计算

### 原理

得到图像特征点后，就可以根据这些特征点实现图像匹配，即得到重叠区域。要把多幅图像拼接成一幅图像，需要以某幅图像为基准，把其他图像映射到该图像所在的平面。映射的关键，就是根据重叠区域的特征点计算图像间的单应矩阵。

### 如何匹配特征点

可以通过最近邻算法（K-NN，K 表示最近邻特征点的数量）得到图像 A 中某一特征点 a 在图像 B 中最相似的 K 个特征点，对这 K 个特征点进行距离比较，最终判断是否找到了 a 的最相似特征点，即配对成功与否。

例如取 K = 2，即 2-NN 算法：图像 B 中与 a 最相似的两个特征点为 b 和 c，设 L_ab 和 L_ac 分别表示 a 与 b、c 的距离（距离通过特征点描述符的比较实现），且 L_ab < L_ac。若满足下式〔公式图〕，则认为 a 的匹配特征点为 b。

### 如何得到这 K（2）个特征点

最简单、最直接的方法是计算 a 与图像 B 中所有特征点的距离，适用于特征点不多的情况。当特征点较多、需要匹配的图像也较多时，采用 K-D 树（K 维树）的算法会更有效。

### K-D 树简介（K-NN 与 K-D 树中的 k 含义不同）

K-D 树是一棵二叉树，树中存储的是 K 维数据，正好对应于特征点的 K 维描述符。K-D 树本质上是对该 K 维数据集合的一种 K 维空间划分，树中的每个叶节点都对应一个 K 维超矩形区域。构建好后，可以迅速得到某一数据所属的超矩形区域，因此非常适合范围搜索和最近邻搜索，但不适合维数太多的数据。需要注意，K-NN 中的 K 和 K-D 树中的 K 含义不同。

构建 K-D 树需要解决两个问题：一是选择哪个维度进行分割；二是如何把数据分割为左分支和右分支。

- 维度选择：通常交替循环地依次选择所有维度。例如 3-D 树中，"父节点"用 x 维，"子节点"用 y 维，"孙节点"用 z 维，"重孙节点"又回到 x 维，以此类推。
- 分割方式：一般选择该维度的中值进行分割，小于该值归左分支，大于该值归右分支。

以上方法可以保证 K-D 树是平衡的，即每个叶节点与根节点的距离基本相同。

K-D 树构建好后，即可搜索待查询数据（图像 A 中的特征点 a）的最近邻数据。过程是：从根节点出发，依据分叉规则（选择分割维度和比较中值），沿搜索路径到达最佳叶节点；再从该叶节点出发回溯搜索路径，看是否有比最佳叶节点更好的节点可以替代它。

形象地说，回溯过程就是以待查询点为中心、以待查询点到最佳叶节点的距离为半径画一个超球体：若该超球体与分割维度所在的超平面相交，则需要搜索超平面的另一侧（另一个分支）；若不相交，则无需再搜索。

### 计算单应矩阵

特征点的匹配点对得到后，即可利用这些匹配点对评估计算图像间的单应矩阵 H。

如果平面到平面之间的映射关系是投影映射，那么一定存在一个非奇异（可逆）的 3×3 矩阵 H：当 p 表示一个平面上的任意一点时，该点映射到另一平面的点即为 Hp。当相机围绕一点旋转得到两幅不同图像时，这两幅图像之间就是投影映射，它们的关系可以用矩阵 H 描述，即图像 A 中的点通过 H 可映射到图像 B 中，从而实现两幅图像的拼接。

需要注意，矩阵 H 乘以任意一个不为零的常数并不改变两平面之间的投影映射关系，因此 H 被认为是只有 8 个自由度的单应矩阵。

设图像 A 中某一点的齐次坐标 p 为 (u, v, 1)，该点在图像 B 中的匹配点的齐次坐标 p′ 为 (u′, v′, 1)，则〔公式图，式 2〕：

> p′ ≅ H · p，即 (u′, v′, 1)ᵀ ≅ H · (u, v, 1)ᵀ

即式（3）〔公式图〕。图像坐标 (x, y) 对应的齐次坐标为 (x, y, 1)，而齐次坐标 (x, y, z) 对应的图像坐标为 (x/z, y/z)。

把式（3）展开得到式（4）〔公式图〕，写成矩阵形式得到式（5）〔公式图〕。

观察式（5）等号左侧的第一个矩阵会发现，第三行是前两行的线性组合，即式（6）〔公式图〕，因此式（5）应改为式（7）〔公式图〕。

一对匹配点对可以列写两个方程，因此要解只有 8 个自由度的矩阵 H，只需四对匹配点，但要求这四对点中任意三点都不能共线〔公式图，式 8〕。

式中 h 是矩阵 H 的向量形式。Ah = 0 称为齐次线性最小二乘问题，因此求解单应矩阵 H 就转换为齐次线性最小二乘问题。该方法也称为直接线性变换（DLT）。

### 如何解方程得到 H

**1. 奇异值分解（SVD）**

对 A 做 SVD〔公式图，式 9〕，则 h 为 V 的最后一列，再把 h 重新整理即可得到 H。

通过式（9）得到 h 的方法非常依赖图像的坐标原点及尺度，会使算法不稳定、产生数值误差。可以通过归一化方法使数值收敛于正确结果。

**2. 归一化方法**

归一化的做法是：先平移图像坐标，使坐标原点为匹配点对的重心；再改变尺度，使匹配点到平移后坐标原点的距离为 1。匹配点对所在的两幅图像，以及它们的横、纵坐标，都必须分别独立进行上述归一化处理。

具体计算过程为式（10）〔公式图〕，式中 n 表示匹配点对数量（此处 n = 4），该式得到坐标平均值，即坐标平移量。式（11）〔公式图〕得到坐标尺度，即匹配点到原点的距离为 1。

若想让距离为 √2（许多文献取此值，OpenCV 取 1），只需再乘以相应系数〔公式图〕。由式（10）和式（11）分别构成两个坐标变换矩阵（式 12），则两幅图像的坐标分别变为式（13），即式（14）。这时用坐标变换后的 4 个匹配点对计算单应矩阵，得到 Ĥ，它与真实坐标下的 H 的关系为式（15）。

式中 T′⁻¹ 很容易由式（12）中的 T′ 得到（式 16）。

归一化直接线性变换的步骤：

1. 由式（10）和式（11）分别计算平移量和尺度；
2. 由式（12）计算坐标变换矩阵；
3. 由式（13）计算坐标变换后的特征点坐标值，代入式（8）得到 A，再经奇异值分解（式 9）得到 h；
4. 由式（15）得到最终的单应矩阵 H。

**3. 更多匹配点对计算**

以上由 4 个匹配点对计算 H。为增加鲁棒性，可以用更多匹配点对计算 H。此时式（8）就是一个超定方程组，因为 A 是 2n×9 的矩阵，而 n > 4，所以 2n > 9。当匹配点对数量很大时，直接用 SVD 并不方便，此时可以直接应用最小二乘法求解。

因为 Ah = 0，其误差平方函数 f(h) 可写为式（17）〔公式图〕。显然应使 f(h) 最小，此时的 h 即为解。对 f(h) 求导并使导数为 0，得到式（18）。

AᵀA 是对称的 9×9 方阵，对 AᵀA 进行特征值分解，则 h 应为 AᵀA 特征值为 0 所对应的特征向量。当然由于存在误差，有时没有特征值恰好为 0，这时 h 取最接近 0 的特征值对应的特征向量。可以证明，对 A 做奇异值分解与对 AᵀA 做特征值分解得到的 h 完全相同，因此特征值分解方法也适用于 n = 4 的情况。

**4. RANSAC（RANdom SAmple Consensus）**

尽管使用了更多匹配点对，但由于误差存在，匹配点对的位置可能出现偏差：既有好的匹配点对（内点），也有不好的匹配点对（外点）。用外点计算得到的 H 肯定不正确，因此应选择内点。区分内外点的常用做法是应用 RANSAC 算法。

RANSAC 的核心思想是：从样本集中抽取多少次，才能保证至少有一次不是外点的概率为 q。据此可得关系式（19）〔公式图〕。

式中 N 表示抽取次数，ε 表示样本集中外点的比例，s 表示每次抽取的样本数量（此处 s = 4，因为求 H 至少需要 4 个匹配点对）。式（19）又可表示为式（20）。

式中 q 是需事先确定的参数，q 越大，得到的内点越可信。ε 由重映射误差方法得到：得到 H 后把图像 A 中的特征点 p 重映射到图像 B 中，计算映射后的点与它的匹配特征点 p′ 之间的几何距离（式 21）。

重映射误差为 d²(p′, Hp)。若该距离小于事先确定的阈值 η，则认为这对匹配点对是内点，否则是外点。计算所有匹配点对，即可统计出内点和外点的数量，从而得到 ε，进而由式（20）得到 N。

**RANSAC 计算单应矩阵 H 的步骤**：

1. 给定参数 q 和 η，以及迭代次数 N；
2. 进入迭代，共迭代 N 次：
   - 随机抽取 4 个匹配点对，要求任意 3 个不共线；
   - 由归一化直接线性变换计算单应矩阵 H；
   - 由重映射误差方法（式 21）计算内点数量，并得到参数 ε；
   - 由式（20）更新最大迭代次数 N；
3. 在迭代循环内取得最大内点数量，由这些内点计算单应矩阵，得到最终的 H。

另外还需要用关系式（22）说明内点数 nᵢ 和匹配点对数量 n_f 之间的关系。若满足式（22），说明图像间有很好的匹配。也可以用匹配置信度 c 衡量（式 23）：

- c 越大越好；若 c 大于 3（3 为实验数据），则认为两幅图像十分接近，可以被看成同一幅图像；
- 若 c 小于一定值，则认为两幅图像没有重叠区域，无法拼接。

应用 RANSAC 的主要目的是得到内点，而用全体内点经特征值分解计算得到的 H 误差较大、比较粗糙，因此还需要更"精细"的方法得到精确的 H。由式（21）可知，H 应使重映射误差（匹配点对之间的几何距离）最小，这是一个参数优化估计求极值的问题。

基于 LM 算法（Levenberg-Marquardt）的优化是目前应用较广泛的无条件约束优化方法，该算法在逼近某个极小点时平方收敛，是专门用于误差平方和最小化的方法。它的特点是高斯牛顿法与梯度下降法的结合：既有高斯牛顿法的快速收敛性，也有梯度下降法的全局搜索特性。

**5. LM 算法精细化**

首先给出误差指标函数，即所有内点重映射误差的平方和（式 24）〔公式图〕。式中 n 表示内点总数，h 是单应矩阵 H 的向量形式，第 i 对匹配点对的重映射误差 eᵢ 为式（25），而 e(h) 为式（26）。

LM 算法迭代计算 h 的公式为式（27）；式中 h 的上标表示迭代次数，步长 Δh 为式（28）。式中 I 为单位矩阵，λ 为大于 0 的常数：

- 若 λ = 0，则 LM 算法退化为高斯牛顿法；
- 若 λ 取值很大，则 LM 算法接近梯度下降法。

因此每迭代成功一步，就把 λ 减小一些，这样在接近误差目标时逐渐与高斯牛顿法相似。

J(h) 为式（24）中 E(h) 的雅可比矩阵。前面已分析过 H 只有 8 个自由度，因此可以把 H 归一化使 h₉ = 1，则 J(h) 只需对 H 中前 8 个元素求偏导数（式 29）。式中 n 表示待拼接的图像数量。

式（30）给出 eᵢ(h)（式 25，其中 h₉ = 1）对 h 的偏导数。由于 eᵢ(h) 由横坐标和纵坐标两个值组成，因此在计算 E(h)（式 24）和 Δh（式 28）时，先分别对横、纵坐标单独进行相关运算，再把两个值相加。

**LM 算法计算 h 的步骤**：

1. 把 RANSAC 最后计算得到的 h 作为 LM 算法的初始 h，并初始化 λ = 0.001；
2. 进入迭代，当满足最大迭代次数，或 Δh 小于一定程度（误差目标函数变化很小）时退出：
   - 由式（28）计算 Δh；
   - 比较 E(h + Δh) 和 E(h)：若 E(h + Δh) < E(h)，则 h ← h + Δh，λ ← λ/10；若 E(h + Δh) > E(h)，则 λ ← 10λ。

### 部分代码

`BestOf2NearestMatcher` 是 `FeaturesMatcher` 的一个子类，作用是用 2-NN 方法（找到两个最近邻特征点）实现特征匹配。`CpuMatcher` 类中的 `match` 函数也负责特征匹配，同样是 `FeaturesMatcher` 的子类。

`findHomography` 函数的作用是得到单应矩阵 H，它有两种重载形式，但最终都执行同一种实现。

---

## 相机自标定

### 原理（坐标系之间的转换）

相机参数的评估也称为相机标定。理解这部分内容，应先从成像原理讲起。

真实物体通过小孔映射到成像平面上，小孔到成像平面的距离称为焦距 f。成像平面上的图像是镜像倒立的，为了研究方便，在小孔和物体之间定义一个虚拟成像平面（后面把该平面也称为成像平面），它与小孔的距离也为焦距。两个成像平面上的图像大小相同，但虚拟成像平面上的图像与原物体方向一致。

以小孔为坐标原点建立三维直角坐标系 XYZ，坐标原点 C 称为相机的光心。成像平面 xy 平行于 XY，距光心 C 为 f，其中 Z 轴定义为光轴，它与成像平面 xy 的交点为 P，因此 CP = f。设空间中任一点 Q 的坐标为 (X, Y, Z)，该点映射到成像平面的点 q 的坐标为 (x, y, f)。

由几何知识可得式（31）〔公式图〕，式中 λ = Z/f，则 x = fX/Z，y = fY/Z。因此空间中 Q 点的三维坐标映射到成像平面二维坐标 q 点，在齐次坐标下的线性映射关系为式（32）。

在成像平面（坐标系 xy）中坐标原点为 P，但图像（设坐标系 uv）的坐标原点一般在左上角，因此两个坐标系之间需要通过平移转换。另外成像平面 xy 的长度单位与相机图像传感器的像素单位不同，需要转换，而且水平和垂直的像素往往不相同，所以横纵轴的转换系数不一致。因此式（32）改写为式（33）。

式中，f_u 和 f_v 为焦距 f 在横纵轴的长度与像素转换，它们之间的关系可写为 f_v = α f_u，(c_x, c_y) 为坐标平移。因为矩阵 K 的参数都基于相机内部自身的参数，因此 K 称为相机内参数，K = T V。

相机除内参数外还有外参数。式（33）中的 (X, Y, Z) 称为相机坐标，它与真实的世界坐标 (X′, Y′, Z′) 之间还存在欧几里得变换，即式（34）。式中 R 为 3×3 旋转矩阵，t 为 3×1 平移向量。式（34）代入式（33）得到式（35），式中 [R|t] 称为相机外参数，M 称为投影矩阵。

如果两幅图像是相机在三角架上通过旋转得到的，则对于同一个世界坐标点 (X′, Y′, Z′)，在两幅图像上的坐标点分别为 (u₀, v₀) 和 (u₁, v₁)，即式（36）。由于相机只做旋转处理（或认为物体离相机很远），则 t₀ = t₁ = 0，从而得到两者的关系式（37）。

式中 R₁₀ 为由图像 1 到图像 0 的相对旋转矩阵，R₁₀ = R₁R₀⁻¹。由式（2）可得式（38）。

为简单起见，设两幅图像相机内参数中的坐标平移都为 0，即 K = V，因为抛弃参数 T 对图像拼接影响不大。评估焦距时还需定义 f₁ᵤ = f₁ᵥ = f₁，f₀ᵤ = f₀ᵥ = f₀，即设图像长宽等像素（式 39），则式（38）表示为式（40），式中 R₁₀ = [r_ij]。

由式（40）即可得到焦距 f₀ 和 f₁。观察矩阵 R₁₀ 可知，其前两行一定有相同的范数并且正交，因此有式（41）（式 42）。由式（41）可得式（43），由式（42）可得式（44）。

两者都得到了 f₀，如何取舍？比较式（43）和式（44）中分母部分的绝对值大小：如果式（43）的分母大，则取分式大的值作为 f₀；否则如果式（44）的分母大，则取值小的作为 f₀。

同理，矩阵 R₁₀ 的前两列也一定有相同的范数并且正交，因此有式（45）（式 46）。由式（45）可得式（47），由式（46）可得式（48）。

同理比较式（47）和式（48）中分母部分的绝对值：如果式（48）的分母大，则取分式大的值作为 f₁；否则如果式（47）的分母大，则取值小的作为 f₁。

如果两幅图像的焦距相同，则最终这两幅图像的焦距 f 为式（49）。当评估计算得到多个 f 时，可取这些 f 的中值作为所有相机的焦距。

焦距得到后，即可由式（38）得到 R₁₀（式 50），进而 R₀₁（由图像 0 到图像 1 的相对旋转矩阵）为式（51），最终得到式（52）。

对于刚性物体，其旋转都是沿笛卡尔坐标系的 x 轴、y 轴和 z 轴进行，分别沿这三个轴的旋转矩阵定义为式（53），则旋转矩阵 R 表示为式（54）。

三维旋转除了用旋转矩阵描述外，还可以用旋转向量 r 描述，即 r = [r_x, r_y, r_z]ᵀ。旋转向量的长度（模）表示绕轴逆时针旋转的角度 θ。旋转向量与旋转矩阵可以通过 Rodrigues 算法转换：

- 由旋转向量转换为旋转矩阵的 Rodrigues 算法：式（55）（式 56）（式 57），式中 I 为 3×3 单位矩阵；
- 由旋转矩阵转换为旋转向量的 Rodrigues 算法：式（58）。

当有多幅图像需要拼接为一幅图像时，要以其中一幅图像为基准，其他图像都旋转到该基准图像平面上，因此需要找到基准平面。这里用到的是最大生成树算法。

待拼接图像的排列是无序的，事先也不知道它们之间的关系，只知道它们之间的单应矩阵（由图像间的内点计算评估得到）。由此可以构造一个无向图 G：G 的节点为图像，G 的边为内点数；然后利用并查集在该图中得到一棵最大生成树。

以图 8 为例：图 (a) 为无向图，节点为图像（A、B、C、D、E），边为内点数；图 (b) 为最大生成树，由图像 C 到图像 B 要经过最大的边连接，因此需经过图像 A，C 与 B 之间的直接连接被去掉。

把树的中心节点作为基准图像。中心节点的确定方法：计算每个节点到所有叶节点的距离，把其中最大值作为该节点的值；再选择这些值中最小者作为中心节点。这里的距离指节点间的节点数。如图 8(c) 所示，节点 A 和 C 为中心节点。中心节点可能是 1 个也可能是 2 个，若为 2 个任选其一即可。

### 光束平差

基于以上方法得到了相机的内外参数，但这样得到的参数忽略了多个图像间的约束，且会产生累计误差。这时需要用光束平差法（Bundle Adjustment）精确化相机参数。

光束指相机光心"发射"出来的光束（射线），它透过相片到达物点，因此相片中的点应与物点处于同一条光束线上；当两者不共线时，就需要对结构和视角参数进行调节，以达到最优解甚至共线的目的。最优化一般采用前述 LM 算法。

应用于光束平差法的 LM 算法，误差指标函数可以有两种：重映射误差和射线发散误差。

### 重投影误差

重投影误差公式为式（25）（即一个内点有 x 轴和 y 轴两个误差值），而单应矩阵 H 由式（38）得到，也就是说 H 由相机的内外参数得到。

相机的内外参数共 7 个：f_u、α、c_x、c_y、r_x、r_y、r_z。前 4 个是内参数（见式 33），后 3 个是外参数（即式 55 中旋转向量的三个元素）。因此式（25）中的 h 为 h(f_u, α, c_x, c_y, r_x, r_y, r_z)，由此得到 J(h) 为式（59）。式中 n 表示待拼接图像数量，即所有相机。

有时为了计算方便，导数可以用差分近似。例如计算 e 对 f_u 的偏导，则用式（60），式中 Δ 表示一个很小的数。

### 射线发散误差

第二种误差指标函数基于射线发散原理：不同相机发出的射线透过相片后到达同一个物点，但由于误差，两条射线不会相交（称两条射线发散了），就把这两条射线间的最短距离 d 定义为射线发散误差。

设 (u₁, v₁, 1) 和 (u₂, v₂, 1) 为两幅不同图像中同一特征点的齐次坐标，则由单应矩阵 H₁ 和 H₂ 分别得到它们的物点坐标为式（61）。分别对坐标做归一化处理（式 62、式 63）。

为简化计算，射线间的最短距离可表示为基于不同焦距 f₁ 和 f₂ 下归一化后的物点坐标之差（式 64）。

式（64）说明，在计算射线发散误差时，每个内点有 x 轴、y 轴和 z 轴三个误差值。

假设射线就是相机的光轴，因此单应矩阵 H 中的参数 α = 1，c_x = c_y = 0，所以式（25）中 h 只是基于 4 个参数的变量，即 h(f_u, r_x, r_y, r_z)，由此得到射线发散误差的 J(h) 为式（65）。

### 波形校正

前述光束平差法会引起波形效应（wavy effect），即拼接图像呈现蛇形分布。这是因为真实拍摄时不大可能都保持水平而不倾斜，也就是重力轴没有垂直于图像。因此需要引入一个全局校正矩阵 Q 用于"拉直"拼接图像，该方法也称为波形校正。

校正的目的是：在第 k 个相机旋转矩阵 R_k 乘以 Q 后，全局 y 轴应与图像的 x 轴垂直。这种约束条件可表示为式（66）。

式中 i = (1, 0, 0)，j = (0, 1, 0)，该式的含义是 R_k 的第一行 r_k0 垂直于 Q 的第二列 q₁。式（66）这类约束问题可看成最小二乘问题（式 67）。因此 q₁ 是矩量矩阵 Σ rᵀr 的最小特征值所对应的特征向量。矩阵 Q 其他列的经验公式为式（68）。

式中，× 表示矩阵的点乘（对应元素相乘，与 MATLAB 中的点乘相同），分母表示对分子取模，即 q₀ 是归一化的结果（式 69）。

最终的全局校正矩阵 Q = [q₀ q₁ q₂]，用 Q 乘以各相机的 R 即实现波形校正。如果要进行垂直校正拉直，则需选择最大特征值对应的特征向量。

### 部分代码

`Estimator` 类是相机参数评估器的基类，`HomographyBasedEstimator` 和 `BundleAdjusterBase` 都是它的子类。`HomographyBasedEstimator` 主要负责相机参数的计算评估，`BundleAdjusterBase` 主要通过光束平差法使相机参数精确化。

`BundleAdjusterBase` 类构造函数的主要作用，是为每个相机参数数量（`num_params_per_cam`）和每个内点误差数量（`num_errs_per_measurement`）赋值，并初始化：

- `setRefinementMask`：表示需要精确化的相机内参数矩阵 K 的掩码矩阵；
- `setConfThresh`：表示内点阈值 `conf_thresh_`（置信度阈值）；
- `setTermCriteria`：表示 LM 算法的迭代终止条件。

- 若为重映射误差：`num_params_per_cam` = 7，`num_errs_per_measurement` = 2（x 轴、y 轴误差）；
- 若为射线发散误差：`num_params_per_cam` = 4，`num_errs_per_measurement` = 3（x、y、z 轴误差）。

`BundleAdjusterBase` 有两个子类 `BundleAdjusterReproj` 和 `BundleAdjusterRay`，分别表示光束平差法的两种实现——重投影方法和射线发散方法，即误差指标函数的两种形式。

原文给出了加光束平差与不加光束平差的效果对比图。

---

## 图像投影变换

### 原理

每幅图像是相机在不同角度下拍摄得到的，并不在同一个投影平面上，如果对重叠部分直接拼接，会破坏实际场景的视觉一致性。因此需要在拼接之前对图像进行投影变换，即对图像进行扭曲变形。

设图像中某像素点的二维坐标为 (x, y)，对应的世界坐标为 (X, Y, Z)，两者关系为〔公式图，式 70〕，式中 R 为旋转矩阵，K 为相机内参数矩阵。

### 1. 平面映射

**图像像素坐标 → 投影图像坐标**：设 (u, v) 为映射后的二维坐标，映射式为式（71）〔公式图〕。

如果世界坐标有平移、投影图像有尺度变化，则式（71）改写为〔公式图〕，式中 s 表示尺度（与相机焦距成正比），t₁、t₂、t₃ 表示三个坐标轴的位移。

**投影图像坐标 → 图像像素坐标**：见原文公式〔公式图〕。

### 2. 柱面投影

- 图像像素坐标 → 投影图像坐标：〔公式图〕
- 投影图像坐标 → 图像像素坐标：〔公式图〕

### 3. 球面投影

- 图像像素坐标 → 投影图像坐标：〔公式图〕
- 投影图像坐标 → 图像像素坐标：〔公式图〕，由 (X, Y, Z) 得到 (x, y) 的公式也是式（74）。

### 部分代码

原文给出了正投影与反投影的实现代码截图〔公式图〕。

---

## 曝光补偿

### 原理

即使通过几何投影做到很好的拼接，如果不同图像之间曝光程度不同，拼接图像的重叠部分也会出现明显边缘，使图像看起来不自然。因此还需要对每幅图像进行曝光补偿，使所有图像具有相同的曝光程度。

常用的曝光补偿方法有增益补偿和分块补偿两种。

**增益补偿**：为每幅图像赋予一个增益系数，使重叠部分的图像强度相等或相似。可利用误差函数实现：

- 式中 g_i 和 g_j 为图像 i 和图像 j 的增益系数，R(i,j) 表示两幅图像的重叠部分，I_i(u_i) 表示图像 i 在重叠部分 R(i,j) 的强度平均值 I_ij（式 82）〔公式图〕；
- 式中 R、G、B 分别表示彩色图像的红、绿、蓝分量强度值，N_ij 表示重叠部分 R(i,j) 的像素数量。

显然当 g = 0 时，式（83）表示的误差函数为零，即最优解——这明显不是想要的值。另外为了提高鲁棒性，增益补偿误差函数的经验公式为式（85）〔公式图〕，式中 σ_N 和 σ_g 分别表示误差和增益的标准差，σ_N = 10（若强度范围为 0～255），σ_g = 0.1。

式（85）是增益系数 g 的一个二次目标函数，可通过令导数为 0 得到闭合形式的解。式（85）对 g_i 的导数为式（86）；使该导数为 0，并展开成以 g₁, g₂, …, g_n 为变量的等式（式 87）。对所有的 g 求导，就建立起 n 个形如式（87）的线性方程；求解该方程组，即可得到 n 幅图像的 n 个增益系数 g。

**分块补偿**：把图像分割成大小相同的块，以块为单位进行增益补偿，即每一块都有一个增益系数。该方法补偿精度更好，还能缓解增益补偿算法的复杂性。块的尺寸经验值为 32×32。

分块补偿的结果会使拼接图像呈现"块"的形态，因此还需要对每一块的增益系数做平滑滤波：对同一幅图像所有块的增益系数应用分割线性滤波，滤波核为 [1/4, 1/2, 1/4]。

### 部分代码

曝光补偿的基类及各子类声明（`ExposureCompensator`、`NoExposureCompensator`、`GainCompensator`、`ChannelsCompensator`、`BlocksCompensator`、`BlocksGainCompensator`、`BlocksChannelsCompensator`）：

```cpp
class CV_EXPORTS_W ExposureCompensator
{
public:
    ExposureCompensator() : updateGain(true) {}
    virtual ~ExposureCompensator() {}

    enum { NO, GAIN, GAIN_BLOCKS, CHANNELS, CHANNELS_BLOCKS };
    CV_WRAP static Ptr<ExposureCompensator> createDefault(int type);

    /**
     * @param corners Source image top-left corners
     * @param images  Source images
     * @param masks   Image masks to update (second value in pair specifies the value which should be used
     *                to detect where image is)
     */
    CV_WRAP void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
                      const std::vector<UMat> &masks);
    /** @overload */
    virtual void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
                      const std::vector<std::pair<UMat, uchar> > &masks) = 0;
    /** @brief Compensate exposure in the specified image.
     *
     * @param index  Image index
     * @param corner Image top-left corner
     * @param image  Image to process
     * @param mask   Image mask
     */
    CV_WRAP virtual void apply(int index, Point corner, InputOutputArray image, InputArray mask) = 0;
    CV_WRAP virtual void getMatGains(CV_OUT std::vector<Mat>&) { CV_Error(Error::StsInternal, ""); };
    CV_WRAP virtual void setMatGains(std::vector<Mat>&) { CV_Error(Error::StsInternal, ""); };
    CV_WRAP void setUpdateGain(bool b) { updateGain = b; };
    CV_WRAP bool getUpdateGain() { return updateGain; };
protected:
    bool updateGain;
};

/** @brief Stub exposure compensator which does nothing. */
class CV_EXPORTS_W NoExposureCompensator : public ExposureCompensator
{
public:
    void feed(const std::vector<Point> &/*corners*/, const std::vector<UMat> &/*images*/,
              const std::vector<std::pair<UMat, uchar> > &/*masks*/) CV_OVERRIDE { }
    CV_WRAP void apply(int /*index*/, Point /*corner*/, InputOutputArray /*image*/, InputArray /*mask*/) CV_OVERRIDE { }
    CV_WRAP void getMatGains(CV_OUT std::vector<Mat>& umv) CV_OVERRIDE { umv.clear(); return; };
    CV_WRAP void setMatGains(std::vector<Mat>& umv) CV_OVERRIDE { umv.clear(); return; };
};

/** @brief Exposure compensator which tries to remove exposure related artifacts by adjusting image
 * intensities, see @cite BL07 and @cite WJ10 for details. */
class CV_EXPORTS_W GainCompensator : public ExposureCompensator
{
public:
    // This Constructor only exists to make source level compatibility detector happy
    CV_WRAP GainCompensator() : GainCompensator(1) {}
    CV_WRAP GainCompensator(int nr_feeds) : nr_feeds_(nr_feeds), similarity_threshold_(1) {}
    void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
              const std::vector<std::pair<UMat, uchar> > &masks) CV_OVERRIDE;
    void singleFeed(const std::vector<Point> &corners, const std::vector<UMat> &images,
                    const std::vector<std::pair<UMat, uchar> > &masks);
    CV_WRAP void apply(int index, Point corner, InputOutputArray image, InputArray mask) CV_OVERRIDE;
    CV_WRAP void getMatGains(CV_OUT std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setMatGains(std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setNrFeeds(int nr_feeds) { nr_feeds_ = nr_feeds; }
    CV_WRAP int getNrFeeds() { return nr_feeds_; }
    CV_WRAP void setSimilarityThreshold(double similarity_threshold) { similarity_threshold_ = similarity_threshold; }
    CV_WRAP double getSimilarityThreshold() const { return similarity_threshold_; }
    void prepareSimilarityMask(const std::vector<Point> &corners, const std::vector<UMat> &images);
    std::vector<double> gains() const;

private:
    UMat buildSimilarityMask(InputArray src_array1, InputArray src_array2);

    Mat_<double> gains_;
    int nr_feeds_;
    double similarity_threshold_;
    std::vector<UMat> similarities_;
};

/** @brief Exposure compensator which tries to remove exposure related artifacts by adjusting image
 * intensities on each channel independently. */
class CV_EXPORTS_W ChannelsCompensator : public ExposureCompensator
{
public:
    CV_WRAP ChannelsCompensator(int nr_feeds = 1) : nr_feeds_(nr_feeds), similarity_threshold_(1) {}
    void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
              const std::vector<std::pair<UMat, uchar> > &masks) CV_OVERRIDE;
    CV_WRAP void apply(int index, Point corner, InputOutputArray image, InputArray mask) CV_OVERRIDE;
    CV_WRAP void getMatGains(CV_OUT std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setMatGains(std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setNrFeeds(int nr_feeds) { nr_feeds_ = nr_feeds; }
    CV_WRAP int getNrFeeds() { return nr_feeds_; }
    CV_WRAP void setSimilarityThreshold(double similarity_threshold) { similarity_threshold_ = similarity_threshold; }
    CV_WRAP double getSimilarityThreshold() const { return similarity_threshold_; }
    std::vector<Scalar> gains() const { return gains_; }

private:
    std::vector<Scalar> gains_;
    int nr_feeds_;
    double similarity_threshold_;
};

/** @brief Exposure compensator which tries to remove exposure related artifacts by adjusting image blocks. */
class CV_EXPORTS_W BlocksCompensator : public ExposureCompensator
{
public:
    BlocksCompensator(int bl_width = 32, int bl_height = 32, int nr_feeds = 1)
        : bl_width_(bl_width), bl_height_(bl_height), nr_feeds_(nr_feeds), nr_gain_filtering_iterations_(2),
          similarity_threshold_(1) {}
    CV_WRAP void apply(int index, Point corner, InputOutputArray image, InputArray mask) CV_OVERRIDE;
    CV_WRAP void getMatGains(CV_OUT std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setMatGains(std::vector<Mat>& umv) CV_OVERRIDE;
    CV_WRAP void setNrFeeds(int nr_feeds) { nr_feeds_ = nr_feeds; }
    CV_WRAP int getNrFeeds() { return nr_feeds_; }
    CV_WRAP void setSimilarityThreshold(double similarity_threshold) { similarity_threshold_ = similarity_threshold; }
    CV_WRAP double getSimilarityThreshold() const { return similarity_threshold_; }
    CV_WRAP void setBlockSize(int width, int height) { bl_width_ = width; bl_height_ = height; }
    CV_WRAP void setBlockSize(Size size) { setBlockSize(size.width, size.height); }
    CV_WRAP Size getBlockSize() const { return Size(bl_width_, bl_height_); }
    CV_WRAP void setNrGainsFilteringIterations(int nr_iterations) { nr_gain_filtering_iterations_ = nr_iterations; }
    CV_WRAP int getNrGainsFilteringIterations() const { return nr_gain_filtering_iterations_; }

protected:
    template <class Compensator>
    void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
              const std::vector<std::pair<UMat, uchar> > &masks);

private:
    UMat getGainMap(const GainCompensator& compensator, int bl_idx, Size bl_per_img);
    UMat getGainMap(const ChannelsCompensator& compensator, int bl_idx, Size bl_per_img);

    int bl_width_, bl_height_;
    std::vector<UMat> gain_maps_;
    int nr_feeds_;
    int nr_gain_filtering_iterations_;
    double similarity_threshold_;
};

class CV_EXPORTS_W BlocksGainCompensator : public BlocksCompensator
{
public:
    // This Constructor only exists to make source level compatibility detector happy
    CV_WRAP BlocksGainCompensator(int bl_width = 32, int bl_height = 32)
        : BlocksGainCompensator(bl_width, bl_height, 1) {}
    CV_WRAP BlocksGainCompensator(int bl_width, int bl_height, int nr_feeds)
        : BlocksCompensator(bl_width, bl_height, nr_feeds) {}

    void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
              const std::vector<std::pair<UMat, uchar> > &masks) CV_OVERRIDE;

    // This function only exists to make source level compatibility detector happy
    CV_WRAP void apply(int index, Point corner, InputOutputArray image, InputArray mask) CV_OVERRIDE {
        BlocksCompensator::apply(index, corner, image, mask); }
    // This function only exists to make source level compatibility detector happy
    CV_WRAP void getMatGains(CV_OUT std::vector<Mat>& umv) CV_OVERRIDE { BlocksCompensator::getMatGains(umv); }
    // This function only exists to make source level compatibility detector happy
    CV_WRAP void setMatGains(std::vector<Mat>& umv) CV_OVERRIDE { BlocksCompensator::setMatGains(umv); }
};

class CV_EXPORTS_W BlocksChannelsCompensator : public BlocksCompensator
{
public:
    CV_WRAP BlocksChannelsCompensator(int bl_width = 32, int bl_height = 32, int nr_feeds = 1)
        : BlocksCompensator(bl_width, bl_height, nr_feeds) {}

    void feed(const std::vector<Point> &corners, const std::vector<UMat> &images,
              const std::vector<std::pair<UMat, uchar> > &masks) CV_OVERRIDE;
};
```

---

## 寻找接缝线

### 原理

拼接图像的另一个重要步骤，是在图像重叠部分内找到一条接缝线——即重叠部分最相似像素的连线。确定接缝线后，在重叠部分，线的一侧只取该侧图像，线的另一侧只取另一侧图像，而不是把重叠部分的两幅图像简单融合。这样可以避免图像模糊及伪像。

常用方法有三种：逐点法、动态规划法和图割法。

**逐点法**：重叠部分内的像素距离哪幅图像更近，就用哪幅图像的相应像素值。该方法并不基于像素值，只基于距离准则，因此不能真正得到最佳接缝线，结果较粗糙，仅能分割重叠部分。

**动态规划法**：可快速找到最佳接缝线，所需内存较少，很适合在移动终端上进行高清晰度全景拼接。

设图像 1 和图像 2 之间有重叠部分，需要得到它们之间的最佳接缝。首先定义重叠部分的误差表面函数（式 88）〔公式图〕，式中 I₁、I₂ 表示两幅图像各自的重叠部分，‖·‖ 表示范数。

接缝线一般有三个限制条件：

1. 若重叠区域宽大于高，则接缝横向走向；若宽小于高，则接缝纵向走向——即要保证接缝线有一定长度；
2. 若为横向接缝，不允许有绝对垂直的接缝线；若为纵向接缝，不允许有绝对水平的接缝线；
3. 重叠区域是矩形，接缝线从矩形一边出发，必须到达与该边平行的另一边结束。

假设重叠区域宽小于高，则接缝线是垂直的，需要横向遍历 e 值，并计算所有可能到达当前像素 (i, j) 的路径的累积最小误差 E（式 89）〔公式图〕。式中 min 中的三项分别表示当前像素与其左上侧、上侧和右上侧的 E。E 中最后一行的最小值表明已到达最小垂直路径的尽头，据此可回溯得到最佳路径，即接缝线。

同理，重叠区域宽大于高时也能做类似计算。

可以看出，动态规划算法非常适合求解式（89），这也是该方法名称的由来。

对于彩色图像，计算式（88）有两种方法：直接法和梯度法。设 p 和 q 是两个相邻像素：

- 直接法公式为式（90）〔公式图〕；
- 梯度法公式为式（91）〔公式图〕，式中 G 表示灰度图像从 p 到 q 方向上的梯度。

**图割法**：主要思想是把图像看成一个有向图 G = {V, E}，V 表示节点，E 表示边。节点分为普通节点和终端节点：图像像素可作为普通节点，图像的聚类结果可看成终端节点，即某一聚类中的普通节点会汇聚于一个终端节点。一般图只有两个终端节点 s 和 t，也就是说一般图割法只能把图像分割成两类 S 和 T。

边分为两类：普通节点之间只有相邻节点可以连接，该边称为 n-links；所有普通节点都可与属于它们的终端节点相连，该边称为 t-links。用最大流图割法可以得到哪些普通节点属于终端节点 s，剩下的普通节点就属于 t。

OpenCV 实现的最大流图割算法基于 Boykov 和 Kolmogorov 提出的算法。它以 s 和 t 为根，以得到两棵不相交的搜索树 S 和 T 为目标，迭代重复以下三个阶段：

1. **生长阶段**：搜索树 S 和 T 生长，直到两棵树生长到一起，形成从 s 到 t 的通路（即流）；
2. **增广阶段**：该通路被增广，使相连的两棵树重新分开；
3. **收养阶段**：搜索树 S 和 T 收养孤立节点（孤儿）。

在生长阶段，如果找不到任何一条从 s 到 t 的通路，迭代结束。在增广阶段，开始时孤儿集合为空，该阶段结束后可能出现孤儿：先得到通路上最小的容量值（边的权值），然后把所有容量值减去该值（通过流量更新容量值），则该通路上至少会有一条权值为 0 的边（饱和边）。

假设饱和边上的两个节点为 p 和 q，且在正向通路中由 p 流向 q：若 p 和 q 都属于树 S，则 q 为孤儿；若 p 和 q 都属于树 T，则 p 为孤儿。

在收养阶段，检查孤儿集合中所有孤儿的邻域像素，连接那些边容量值不为 0 的像素；如果最终可以从该孤儿连通到 s 或 t，则该孤儿属于树 S 或树 T，否则由该孤儿组成的树属于自由节点集。下次迭代的生长阶段，树 S 和树 T 通过连接自由节点集中的节点来生长。

用最大流图割法寻找接缝线的方法是：接缝线把重叠区域分割成两个区域，通过最大流图割法得到树 S 和树 T，即得到被分割的两个区域，区域之间的接缝线自然可得。

普通节点之间的初始容量值（边的权值）也可用直接法和梯度法计算，公式分别与式（90）和式（91）相同。终端节点与普通节点之间的初始容量值可定义为一个很大的值：当重叠区域中的节点只属于图像 1 或图像 2 时，节点与相应终端节点的边赋予该值，其他情况置为 0，表示它们与终端节点无直接连接。

### 接缝线

原文给出的实现思路是替换成 `getMask` 函数、直接居中选取 mask。

---

## 融合

### 原理

上一步虽然已得到接缝线，但如果只是简单地在接缝线两侧选取不同图像，重叠区域在接缝线处的过渡会出现不连贯，视觉上显得突兀。因此还需要在接缝线两侧对不同图像做融合处理。常用的两种方法是羽化和多频段融合。

### 羽化

羽化的原理是对边界进行平滑虚化，通过渐变方法达到自然衔接的效果。应用于图像拼接时，羽化只对接缝线两侧的区域进行处理，公式为式（92）〔公式图〕。

式中 R 表示 n 幅图像重叠后经羽化处理得到的新图像，I_i 表示第 i 幅图像在接缝线两侧区域内的部分，w_i 表示 I_i 的权值，它是当前像素到达第 i 幅图像最近边界的距离。

从式（92）可以看出，羽化算法本质上就是加权平均的过程。另外羽化处理还可以通过设置锐度参数，来调整羽化平滑处理的虚化程度和羽化面积。

### 多频段融合

羽化算法虽然简单，但当重叠部分有细微不重合时，图像的高频部分会出现明显模糊。

为了保留图像的高频成分（细节部分），需要应用多频段融合方法：通过建立拉普拉斯（带通滤波器）金字塔，使各个频段上的信息都保留并融合在一起。

具体步骤：

1. 分别建立各幅图像的拉普拉斯金字塔；
2. 针对重叠区域，把各金字塔的相同层应用式（92）合并；
3. 对合并后的金字塔做逆拉普拉斯变换，得到最终的融合图像。

拉普拉斯金字塔通过高斯金字塔得到：高斯金字塔的上一层图像，是对下一层图像做高斯模糊（卷积高斯内核）再降采样（隔点采样）得到的；拉普拉斯金字塔的各层图像，则是由高斯金字塔的相同层减去它上一层的扩展（先升采样，再卷积高斯内核）得到的。

### 部分代码

`Blend` 类为基类，其子类对应羽化、多频段等不同融合方式。

---

## 全景拼图效果

原文以两组实拍拼接结果作为效果展示。
