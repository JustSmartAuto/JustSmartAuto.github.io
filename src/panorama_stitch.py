# -*- coding: utf-8 -*-
"""Lab7 全景图拼接的 Python/OpenCV 实现与测试。

对应 hw7_pa.h 的接口：
    bool CylindricalPanorama::makePanorama(vector<Mat>& img_vec, Mat& img_out, double f)

流程（与 Lab7 要求一致）：
    1) 柱面坐标正/反变换 + 双线性插值，把每幅平面图像投影为柱面图像；
    2) 对相邻柱面图像做 SIFT 特征提取与 2-NN 匹配，RANSAC 求平移变换；
    3) 按累计平移把各图贴到同一画布，接缝处按到边界的距离做羽化加权融合。

用法：
    uv run --with opencv-python --with numpy python panorama_stitch.py
    uv run --with opencv-python --with numpy python panorama_stitch.py --data <目录> --out <输出.png>
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

import cv2
import numpy as np


# ----------------------------------------------------------------------------
# 1. 柱面投影
# ----------------------------------------------------------------------------
def cylindrical_warp(img: np.ndarray, f: float, border_value=0):
    """把平面图像投影到半径为 f 的柱面。

    x' = f * arctan(x / f)
    y' = f * y / sqrt(x^2 + f^2)

    采用逆向映射：对目标柱面图上的每个像素求其在原平面图上的位置，再双线性插值，
    避免正向映射产生的空洞。
    """
    h, w = img.shape[:2]
    r = float(f)
    # 柱面展开后的宽度（原图左右边界对应 x = ±w/2）
    w_out = int(round(2.0 * r * np.arctan((w / 2.0) / r)))
    h_out = h  # r = f 时，|y'| 最大为 h/2，纵向尺寸不变

    # 目标坐标（原点移到图像中心）
    xs = np.arange(w_out, dtype=np.float32) - (w_out - 1) / 2.0
    ys = np.arange(h_out, dtype=np.float32) - (h_out - 1) / 2.0
    grid_x, grid_y = np.meshgrid(xs, ys)

    # 逆变换：柱面坐标 -> 平面坐标
    x = r * np.tan(grid_x / r)
    y = grid_y * np.sqrt(x * x + r * r) / r

    # 平面坐标 -> 原图像素坐标（原点移回左上角）
    map_x = (x + (w - 1) / 2.0).astype(np.float32)
    map_y = (y + (h - 1) / 2.0).astype(np.float32)

    if img.ndim == 2:
        out = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=border_value)
    else:
        out = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT,
                        borderValue=tuple([border_value] * img.shape[2]))
    return out


# ----------------------------------------------------------------------------
# 2. 特征提取与相邻图像变换估计
# ----------------------------------------------------------------------------
def detect_and_match(img1, img2, ratio=0.75, min_good=20):
    """SIFT 特征 + 2-NN(Lowe ratio) 匹配 + RANSAC 相似变换。

    返回 M（2x3），含义为 p2 = M · p1，即把图像 1 的坐标映射到图像 2。
    """
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if img1.ndim == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if img2.ndim == 3 else img2

    sift = cv2.SIFT_create(nfeatures=4000)
    # 屏蔽柱面投影后留下的黑色边界，避免在无纹理区域产生伪特征点
    kp1, des1 = sift.detectAndCompute(gray1, (gray1 > 0).astype(np.uint8) * 255)
    kp2, des2 = sift.detectAndCompute(gray2, (gray2 > 0).astype(np.uint8) * 255)
    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return None, 0, 0

    bf = cv2.BFMatcher(cv2.NORM_L2)
    knn = bf.knnMatch(des1, des2, k=2)
    good = [m for pair in knn if len(pair) == 2
            for m, n in [pair] if m.distance < ratio * n.distance]
    if len(good) < min_good:
        return None, len(good), 0

    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    # 柱面展开后，相机旋转表现为平移 + 少量残余旋转/尺度
    model, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0,
        maxIters=5000, confidence=0.995)
    if model is None or inliers is None:
        return None, len(good), 0
    return model.astype(np.float64), len(good), int(inliers.sum())


def refine_pair(gray1, gray2, init_warp, iters=100):
    """以 SIFT 结果为初值，用 ECC（欧氏变换）做亚像素精化。

    按 OpenCV 的约定，findTransformECC 返回的 warp 满足：
        输入图像坐标 = warp · 模板图像坐标
    也就是与 detect_and_match 返回的 model（p2 = M·p1）同向，可直接作为初值。
    """
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-6)
    mask = (gray2 > 0).astype(np.uint8) * 255
    try:
        _, warp = cv2.findTransformECC(
            gray1.astype(np.float32), gray2.astype(np.float32),
            init_warp.astype(np.float32), cv2.MOTION_EUCLIDEAN,
            criteria, mask, 5)
        return warp.astype(np.float64)
    except cv2.error:
        return init_warp


def _to_h(mat2x3):
    """2x3 仿射矩阵扩展为 3x3 齐次矩阵。"""
    h = np.eye(3, dtype=np.float64)
    h[:2, :] = mat2x3
    return h


# ----------------------------------------------------------------------------
# 3. 羽化融合
# ----------------------------------------------------------------------------
def _crop_black(img, tol=8):
    """裁掉四周的全黑（无效）区域。"""
    gray = img.max(axis=2)
    mask = gray > tol
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return img
    return img[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def stitch(images, f, ramp=40, blend="seam", sharpness=8, refine=True,
           use_rotation=True, straighten=True):
    """拼接流程：柱面投影 -> 相邻变换估计（SIFT+RANSAC，可选 ECC 精化）
    -> 变换链累积到同一画布 -> 融合。

    blend="seam"   : 权重取 sharpness 次幂，等效于接缝式合成（每个像素基本只由一幅图贡献），
                     避免多层平均造成的重影；
    blend="feather": 线性羽化加权平均，重叠层多时会出现模糊/重影。
    """
    warped = [cylindrical_warp(img, f) for img in images]
    grays = [cv2.cvtColor(w, cv2.COLOR_BGR2GRAY) for w in warped]

    # 第 i 幅图到画布坐标的齐次变换（从第 0 幅起逐级累积）
    transforms = [np.eye(3, dtype=np.float64)]
    for i in range(1, len(warped)):
        model, n_good, n_in = detect_and_match(warped[i - 1], warped[i])
        if model is None:
            print("  [warn] 图 %d-%d 匹配失败，按单位变换处理" % (i - 1, i))
            model = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        else:
            print("  图 %2d -> %2d: 匹配 %4d 对，内点 %4d，dx=%7.2f dy=%6.2f rot=%5.2f°"
                  % (i - 1, i, n_good, n_in, model[0, 2], model[1, 2],
                     np.degrees(np.arctan2(model[1, 0], model[0, 0]))))

        # model 把图 i-1 的坐标映射到图 i
        model = model.astype(np.float64)
        if refine:
            refined = refine_pair(grays[i - 1], grays[i], model)
            ang0 = np.degrees(np.arctan2(model[1, 0], model[0, 0]))
            ang1 = np.degrees(np.arctan2(refined[1, 0], refined[0, 0]))
            # ECC 可能被重复纹理误导，偏离 SIFT 结果过大时不采用
            if (abs(refined[0, 2] - model[0, 2]) < 20 and
                    abs(refined[1, 2] - model[1, 2]) < 20 and
                    abs(ang1 - ang0) < 2.0):
                model = refined
                print("       ECC 精化: dx=%7.2f dy=%6.2f rot=%5.2f°"
                      % (model[0, 2], model[1, 2], ang1))
            else:
                print("       ECC 结果偏离过大，保留 SIFT 结果")

        # 需要的是把图 i 的坐标映射到图 i-1 坐标系
        warp = cv2.invertAffineTransform(model)
        if not use_rotation:
            # 只保留平移分量（Lab 的柱面投影假设：纯旋转只产生平移）
            warp = np.array([[1.0, 0.0, warp[0, 2]], [0.0, 1.0, warp[1, 2]]])
        transforms.append(transforms[-1] @ _to_h(warp))

    # 波形校正：各幅图相对画布的旋转角取均值，把整幅全景反向旋转拉直
    if straighten and use_rotation:
        angles = [np.arctan2(t[1, 0], t[0, 0]) for t in transforms]
        theta = float(np.mean(angles))
        c, s = np.cos(-theta), np.sin(-theta)
        straight = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        transforms = [straight @ t for t in transforms]
        print("  波形校正：全局旋转 %.2f°" % np.degrees(-theta))

    # 画布范围：把所有图的四角变换到画布坐标后取外接矩形
    corners = []
    for h_mat, img in zip(transforms, warped):
        h, w = img.shape[:2]
        pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        corners.append(cv2.transform(pts, h_mat[:2]).reshape(-1, 2))
    all_pts = np.vstack(corners)
    min_xy = np.floor(all_pts.min(axis=0))
    max_xy = np.ceil(all_pts.max(axis=0))
    canvas_w = int(max_xy[0] - min_xy[0])
    canvas_h = int(max_xy[1] - min_xy[1])
    shift = np.array([[1.0, 0.0, -min_xy[0]], [0.0, 1.0, -min_xy[1]]])

    acc = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
    acc_w = np.zeros((canvas_h, canvas_w, 1), dtype=np.float32)

    for h_mat, img in zip(transforms, warped):
        mat = _to_h(shift) @ h_mat
        warped_img = cv2.warpAffine(img, mat[:2], (canvas_w, canvas_h),
                                    flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        valid_u8 = (warped_img.sum(axis=2) > 0).astype(np.uint8)
        # 权重 = 当前像素到本图有效区域边界的距离，在 ramp 像素内线性渐变（羽化）
        dist = cv2.distanceTransform(valid_u8, cv2.DIST_L2, 3)
        weight = np.clip(dist / max(ramp, 1), 0.0, 1.0)[..., None]
        if blend == "seam":
            weight = weight ** sharpness
        acc += warped_img.astype(np.float32) * weight
        acc_w += weight

    result = np.divide(acc, np.maximum(acc_w, 1e-6))
    result[acc_w[..., 0] < 1e-6] = 0
    result = _crop_black(np.clip(result, 0, 255).astype(np.uint8))
    return result, transforms


# ----------------------------------------------------------------------------
# 4. 入口
# ----------------------------------------------------------------------------
def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    """OpenCV 的 imread 在中文路径下会失败，改用 imdecode 读取。"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, flags)


def imwrite_unicode(path, img):
    """OpenCV 的 imwrite 在中文路径下会静默失败，改用 imencode + 文件写入。"""
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise IOError("图像编码失败: %s" % path)
    buf.tofile(path)


def load_dataset(folder):
    with open(os.path.join(folder, "K.txt"), "r", encoding="utf-8") as fh:
        focal = float(fh.read().strip())
    # 注意：Windows 下 glob 不区分大小写，"*.JPG" 与 "*.jpg" 会命中同一批文件，
    # 这里统一遍历目录再按扩展名过滤，避免重复读入。
    exts = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
    paths = sorted(p for p in glob.glob(os.path.join(folder, "*"))
                   if os.path.isfile(p) and p.lower().endswith(exts))
    images = [imread_unicode(p) for p in paths]
    images = [im for im in images if im is not None]
    return images, focal, paths


def run(folder, out_path, max_width=0, ramp=40, blend="seam", refine=True, use_rotation=True):
    images, focal, paths = load_dataset(folder)
    print("数据集: %s" % folder)
    print("  图像 %d 张，尺寸 %dx%d，焦距 f=%.2f" %
          (len(images), images[0].shape[1], images[0].shape[0], focal))

    panorama, transforms = stitch(images, focal, ramp=ramp, blend=blend, refine=refine,
                                  use_rotation=use_rotation)

    if max_width and panorama.shape[1] > max_width:
        scale = max_width / panorama.shape[1]
        panorama = cv2.resize(panorama, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    imwrite_unicode(out_path, panorama)
    print("  全景图: %dx%d -> %s" % (panorama.shape[1], panorama.shape[0], out_path))
    return panorama, transforms


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Lab7 全景拼接测试")
    parser.add_argument("--data", help="数据目录（含图片与 K.txt）")
    parser.add_argument("--out", help="输出图片路径")
    parser.add_argument("--ramp", type=int, default=40, help="羽化渐变宽度，默认 40")
    parser.add_argument("--blend", choices=("seam", "feather"), default="seam",
                        help="融合方式：seam=接缝式（默认），feather=线性羽化平均")
    parser.add_argument("--max-width", type=int, default=0, help="输出限宽，0 表示不缩放")
    parser.add_argument("--no-refine", action="store_true", help="关闭 ECC 亚像素精化")
    parser.add_argument("--no-rotation", action="store_true", help="只使用平移分量，忽略逐帧旋转")
    args = parser.parse_args(argv)

    if args.data:
        out = args.out or os.path.join(here, "output", "panorama.png")
        run(args.data, out, args.max_width, args.ramp, args.blend,
            not args.no_refine, not args.no_rotation)
        return 0

    data1 = os.path.join(here, "panorama-data1")
    data2 = os.path.join(here, "panorama-data2")
    missing = [d for d in (data1, data2) if not os.path.isdir(d)]
    if missing:
        print("缺少数据目录：%s" % ", ".join(missing))
        print("请先解压 panorama-data1.zip / panorama-data2.zip 到本目录。")
        return 1

    run(data1, os.path.join(here, "output", "panorama-data1.png"), args.max_width, args.ramp,
        args.blend, not args.no_refine, not args.no_rotation)
    print()
    run(data2, os.path.join(here, "output", "panorama-data2.png"), args.max_width, args.ramp,
        args.blend, not args.no_refine, not args.no_rotation)
    return 0


if __name__ == "__main__":
    sys.exit(main())
