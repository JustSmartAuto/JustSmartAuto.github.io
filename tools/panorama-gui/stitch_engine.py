"""全景融合核心：中文路径安全的读写 + 自动/手动两种融合模式。

- 自动排序：OpenCV Stitcher（特征匹配自动确定顺序，多频段融合）
- 手动排序：按列表顺序用 ORB + RANSAC 相似变换累积，距离羽化融合
"""
import os
import cv2
import numpy as np

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_CANVAS_PIXELS = 400_000_000  # 画布面积上限（与项目约定一致）


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMG_EXTS


def imread_unicode(path):
    """中文路径安全读图（fromfile + imdecode）。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path, img, quality=95):
    """中文路径安全写图；JPEG 质量默认 95。"""
    ext = os.path.splitext(path)[1].lower() or ".jpeg"
    params = []
    if ext in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        return False
    buf.tofile(path)
    return True


# ---------- 自动排序：OpenCV Stitcher ----------
def stitch_auto(images):
    """返回 (status, pano)。"""
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    return stitcher.stitch(images)


STATUS_MSG = {
    0: "融合成功",
    1: "图像不足或重叠不够，无法拼接（ERR_NEED_MORE_IMGS）",
    2: "特征匹配失败，估计不出单应矩阵（ERR_HOMOGRAPHY_EST_FAIL）",
    3: "相机参数调整失败（ERR_CAMERA_PARAMS_ADJUST_FAIL）",
}


def status_msg(status):
    return STATUS_MSG.get(status, f"未知错误（status={status}）")


# ---------- 手动排序：ORB 逐对配准 + 距离羽化融合 ----------
def _match_homography(img_a, img_b):
    """求把 img_b 映射到 img_a 坐标系的单应矩阵；返回 (H, inliers)。"""
    orb = cv2.ORB_create(nfeatures=6000, scaleFactor=1.2, nlevels=8)
    ga = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)
    kpa, da = orb.detectAndCompute(ga, None)
    kpb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(kpa) < 8 or len(kpb) < 8:
        return None, 0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = bf.knnMatch(db, da, k=2)
    good = [m for m, n in matches if m.distance < 0.75 * n.distance]
    if len(good) < 10:
        return None, len(good)
    pts_b = np.float32([kpb[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_a = np.float32([kpa[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(pts_b, pts_a, cv2.RANSAC, 5.0)
    return H, (int(mask.sum()) if mask is not None else 0)


def stitch_manual(images):
    """按给定顺序融合；失败抛 ValueError。"""
    n = len(images)
    if n == 0:
        raise ValueError("没有可融合的图片")
    if n == 1:
        return images[0]

    Hs = [np.eye(3, dtype=np.float64)]
    for i in range(1, n):
        H, inliers = _match_homography(images[i - 1], images[i])
        if H is None or inliers < 8:
            raise ValueError(
                f"第 {i} 张与第 {i + 1} 张匹配失败（内点 {inliers}），"
                "相邻画面重叠不足，请尝试自动排序或检查图片顺序"
            )
        Hs.append(Hs[i - 1] @ H)

    # 计算全局画布范围
    all_corners = []
    for i, img in enumerate(images):
        h_i, w_i = img.shape[:2]
        corners = np.float32([[0, 0], [w_i, 0], [w_i, h_i], [0, h_i]]).reshape(-1, 1, 2)
        tc = cv2.perspectiveTransform(corners, Hs[i])
        all_corners.append(tc.reshape(-1, 2))
    pts = np.vstack(all_corners)
    min_x, min_y = pts[:, 0].min(), pts[:, 1].min()
    max_x, max_y = pts[:, 0].max(), pts[:, 1].max()
    if (max_x - min_x) * (max_y - min_y) > MAX_CANVAS_PIXELS:
        raise ValueError("拼接画布过大（超过 4 亿像素），已终止任务")
    canvas_w = int(np.ceil(max_x - min_x))
    canvas_h = int(np.ceil(max_y - min_y))
    T = np.array([[1, 0, -min_x], [0, 1, -min_y], [0, 0, 1]], dtype=np.float64)

    acc = np.zeros((canvas_h, canvas_w, 3), dtype=np.float64)
    wsum = np.zeros((canvas_h, canvas_w), dtype=np.float64)
    for i, img in enumerate(images):
        Ht = T @ Hs[i]
        warped = cv2.warpPerspective(
            img, Ht, (canvas_w, canvas_h),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        # 用全 1 图 warp 得到精确有效区域掩膜（不受像素本身是否为纯黑影响）
        h_i, w_i = img.shape[:2]
        mask_warped = cv2.warpPerspective(
            np.full((h_i, w_i), 255, np.uint8), Ht, (canvas_w, canvas_h))
        mask = (mask_warped > 0).astype(np.uint8)
        weight = cv2.distanceTransform(mask, cv2.DIST_L2, 5).astype(np.float64)
        weight[mask == 0] = 0.0
        acc += warped.astype(np.float64) * weight[..., None]
        wsum += weight

    wsum[wsum < 1e-6] = 1e-6
    pano = (acc / wsum[..., None]).astype(np.uint8)
    return pano
