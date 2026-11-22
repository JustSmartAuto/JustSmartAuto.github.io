# -*- coding: utf-8 -*-
"""型号A 顶面相机（TopCam1~4）SIFT 特征匹配全景拼接。

输入：LineAVI_*_TopCam{1..4}.jpg（5472x3648）
输出：output/topcam_panorama.jpg、output/match_*.jpg、output/stats.json

用法：
    uv run --no-project --with opencv-python --with numpy python stitch_topcam.py
    uv run --no-project --with opencv-python --with numpy python stitch_topcam.py --scale 1.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

# 由匹配诊断确定的物理排列顺序（左 -> 右）：
#   TopCam4 - TopCam3 - TopCam2 - TopCam1
# 依据：相邻对 SIFT 内点数 21/81/125，而 4 与 1、2 的方向组合只有 5~8 个一致点。
ORDER = [4, 3, 2, 1]

RATIO = 0.75
RANSAC_THRESH = 4.0
MIN_INLIERS = 20


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_u(path, img, quality=92):
    ext = os.path.splitext(path)[1].lower() or ".jpg"
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise IOError("编码失败: %s" % path)
    buf.tofile(path)


def sift_features(img, nfeatures=20000):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    sift = cv2.SIFT_create(nfeatures=nfeatures, contrastThreshold=0.004)
    return sift.detectAndCompute(clahe.apply(gray), None)


def match_pair(img_a, img_b, gray_a=None, gray_b=None):
    """返回最优匹配结果 dict(M, good, inliers, cc, ...)。

    M 满足 p_b = M · p_a（相似变换，已做 ECC 精化）。
    先用整幅图和「A 右侧 x% vs B 左侧 x%」多个局部区域求 RANSAC 初始解，
    再用 ECC 相关系数（cc）挑最优——重复工装纹理会让内点数失效，像素相关性更可靠。
    """
    if gray_a is None:
        gray_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    if gray_b is None:
        gray_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)

    wa, wb = img_a.shape[1], img_b.shape[1]
    candidates = [(img_a, img_b, 0, 0)]
    for frac in (0.4, 0.5, 0.6):
        xa = int(wa * (1 - frac))
        xb = int(wb * frac)
        candidates.append((img_a[:, xa:], img_b[:, :xb], xa, 0))

    best = None
    for a, b, off_a, off_b in candidates:
        ka, da = sift_features(a)
        kb, db = sift_features(b)
        if da is None or db is None:
            continue
        bf = cv2.BFMatcher(cv2.NORM_L2)
        knn = bf.knnMatch(da, db, k=2)
        good = [m for m, n in knn if m.distance < RATIO * n.distance]
        if len(good) < 12:
            continue
        src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        M, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_THRESH,
            maxIters=50000, confidence=0.9995)
        if M is None or inl is None:
            continue
        n_in = int(inl.sum())
        if n_in < MIN_INLIERS:
            continue
        M = M.copy()
        M[0, 2] += off_b - off_a
        refined, cc = refine_ecc(gray_a, gray_b, M)
        print("    [候选 %s] inliers=%3d cc=%.4f dx=%.1f dy=%.1f"
              % ("全幅" if off_a == 0 else "右%.0f%%" % ((1 - off_a / wa) * 100),
                 n_in, cc, refined[0, 2], refined[1, 2]))
        cand = dict(M=refined, cc=cc, good=len(good), inliers=n_in,
                    src=src + [off_a, 0], dst=dst + [off_b, 0],
                    mask=inl.ravel() == 1, kp_a=len(ka), kp_b=len(kb))
        if best is None or cand["cc"] > best["cc"]:
            best = cand
    return best


def refine_ecc(gray_a, gray_b, init_model, iters=100):
    """ECC 欧氏变换亚像素精化，返回 (M, 相关系数)。

    OpenCV 约定：输入图像坐标 = warp · 模板图像坐标，与 p_b = M · p_a 同向。
    """
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-6)
    mask = np.full(gray_b.shape, 255, np.uint8)
    try:
        cc, warp = cv2.findTransformECC(
            gray_a.astype(np.float32), gray_b.astype(np.float32),
            init_model.astype(np.float32), cv2.MOTION_EUCLIDEAN, criteria, mask, 5)
        return warp.astype(np.float64), float(cc)
    except cv2.error:
        return init_model, -1.0


def stitch(images, order, scale=0.5, ramp=80, sharpness=4):
    imgs = {}
    for i in order:
        img = images[i]
        if scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        imgs[i] = img

    transforms = [np.eye(3)]
    pairs = []
    for k in range(1, len(order)):
        prev_id, cur_id = order[k - 1], order[k]
        a, b = imgs[prev_id], imgs[cur_id]
        print("[%d] 匹配 TopCam%d -> TopCam%d ..." % (k, prev_id, cur_id))
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        res = match_pair(a, b, ga, gb)
        if res is None:
            raise RuntimeError("TopCam%d 与 TopCam%d 匹配失败" % (prev_id, cur_id))

        model = res["M"]
        warp = cv2.invertAffineTransform(model)  # 把 B 映射到 A 的坐标系

        ang = np.degrees(np.arctan2(model[1, 0], model[0, 0]))
        scl = float(np.hypot(model[0, 0], model[1, 0]))
        t = transforms[-1] @ np.vstack([warp, [0, 0, 1]])
        transforms.append(t)

        overlap_px = a.shape[1] - abs(model[0, 2]) * 1.0
        info = dict(
            from_cam=prev_id, to_cam=cur_id,
            keypoints_a=res["kp_a"], keypoints_b=res["kp_b"],
            good=res["good"], inliers=res["inliers"],
            inlier_ratio=round(res["inliers"] / res["good"], 3),
            ecc_cc=round(float(res["cc"]), 4),
            dx=round(float(model[0, 2]), 2), dy=round(float(model[1, 2]), 2),
            rotation_deg=round(float(ang), 4), scale=round(scl, 5),
            overlap_px=round(float(a.shape[1] - abs(model[0, 2])), 1),
            overlap_ratio=round(float((a.shape[1] - abs(model[0, 2])) / a.shape[1]), 3),
            src_pts=res["src"].reshape(-1, 2), dst_pts=res["dst"].reshape(-1, 2),
            mask=res["mask"],
        )
        pairs.append(info)
        print("    good=%d inliers=%d dx=%.1f dy=%.1f rot=%.2f° scale=%.4f overlap=%.1f%%"
              % (info["good"], info["inliers"], info["dx"], info["dy"],
                 info["rotation_deg"], info["scale"], info["overlap_ratio"] * 100))

    # 画布范围
    corners = []
    for t, cid in zip(transforms, order):
        h, w = imgs[cid].shape[:2]
        pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        corners.append(cv2.transform(pts, t[:2]).reshape(-1, 2))
    allp = np.vstack(corners)
    mn, mx = np.floor(allp.min(0)), np.ceil(allp.max(0))
    cw, ch = int(mx[0] - mn[0]), int(mx[1] - mn[1])
    shift = np.array([[1, 0, -mn[0]], [0, 1, -mn[1]], [0, 0, 1]], dtype=np.float64)
    print("画布: %dx%d" % (cw, ch))

    acc = np.zeros((ch, cw, 3), np.float32)
    acc_w = np.zeros((ch, cw, 1), np.float32)
    for t, cid in zip(transforms, order):
        mat = (shift @ t)[:2]
        placed = cv2.warpAffine(imgs[cid], mat, (cw, ch), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        valid = (placed.sum(2) > 0).astype(np.uint8)
        dist = cv2.distanceTransform(valid, cv2.DIST_L2, 5)
        weight = (np.clip(dist / ramp, 0, 1) ** sharpness)[..., None]
        acc += placed.astype(np.float32) * weight
        acc_w += weight

    pano = np.divide(acc, np.maximum(acc_w, 1e-6))
    pano[acc_w[..., 0] < 1e-6] = 0
    pano = np.clip(pano, 0, 255).astype(np.uint8)

    # 裁黑边
    mask = pano.max(2) > 6
    rows = np.where(mask.any(1))[0]
    cols = np.where(mask.any(0))[0]
    pano = pano[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    return pano, pairs, imgs


def draw_matches(imgs, pairs, out_dir):
    """把内匹配点画到两图并排的画布上。"""
    paths = []
    for p in pairs:
        a = imgs[p["from_cam"]]
        b = imgs[p["to_cam"]]
        h = max(a.shape[0], b.shape[0])
        canvas = np.zeros((h, a.shape[1] + b.shape[1], 3), np.uint8)
        canvas[:a.shape[0], :a.shape[1]] = a
        canvas[:b.shape[0], a.shape[1]:] = b
        for (xa, ya), (xb, yb), keep in zip(p["src_pts"], p["dst_pts"], p["mask"]):
            if not keep:
                continue
            cv2.line(canvas, (int(xa), int(ya)),
                     (int(xb) + a.shape[1], int(yb)), (0, 255, 0), 2)
        canvas = cv2.resize(canvas, None, fx=0.22, fy=0.22, interpolation=cv2.INTER_AREA)
        path = os.path.join(out_dir, "match_%d_%d.jpg" % (p["from_cam"], p["to_cam"]))
        imwrite_u(path, canvas, 85)
        paths.append(path)
    return paths


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="TopCam1~4 SIFT 拼接")
    parser.add_argument("--src", default=os.path.dirname(here),
                        help="原始图像目录（默认脚本上一级）")
    parser.add_argument("--out", default=os.path.join(here, "output"))
    parser.add_argument("--scale", type=float, default=0.5, help="处理尺度，默认 0.5")
    parser.add_argument("--ramp", type=int, default=80, help="羽化宽度（像素）")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    images = {}
    for i in (1, 2, 3, 4):
        p = os.path.join(args.src, "LineAVI_20261118_185339_TopCam%d.jpg" % i)
        img = imread_u(p)
        if img is None:
            raise IOError("读取失败: %s" % p)
        images[i] = img
        print("载入 TopCam%d: %dx%d" % (i, img.shape[1], img.shape[0]))

    pano, pairs, scaled = stitch(images, ORDER, scale=args.scale, ramp=args.ramp)
    pano_path = os.path.join(args.out, "topcam_panorama.jpg")
    imwrite_u(pano_path, pano, 92)
    print("全景图: %dx%d -> %s" % (pano.shape[1], pano.shape[0], pano_path))

    draw_matches(scaled, pairs, args.out)

    stats = dict(
        order=ORDER, scale=args.scale, ramp=args.ramp,
        images=[dict(cam=i, width=images[i].shape[1], height=images[i].shape[0]) for i in ORDER],
        pairs=[{k: v for k, v in p.items() if k not in ("src_pts", "dst_pts", "mask")}
               for p in pairs],
        panorama=dict(width=pano.shape[1], height=pano.shape[0]),
    )
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print("统计: %s" % os.path.join(args.out, "stats.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
