"""全景融合核心：对齐 src/JustSmart.PanoramaWorker/PanoramaStitcher.cs。

两种融合算法（由 algorithm 参数选择）：
- BASELINE：SIFT + 4 候选区域 + RANSAC 相似变换 + ECC 精化 -> 变换链 -> 距离羽化加权
- DEGHOST ：在 Baseline 配准结果上做「增益匹配 + 最佳缝合线 DP + smoothstep 融合」

所有耗时步骤通过 progress(fraction, text) 上报进度，供 UI 显示进度条与预估时间。
"""
import os

import cv2
import numpy as np

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_CANVAS_PIXELS = 400_000_000  # 画布面积上限（与项目约定一致）

# ---- 与 C# PanoramaStitcher 一致的常量 ----
SCALE = 0.5
RATIO = 0.75
RANSAC_THRESHOLD = 4.0
MIN_INLIERS = 20
MAX_FEATURES = 20000
MAX_ECC_SHIFT_PX = 150.0
MAX_ECC_ROT_DEG = 5.0
RAMP = 80
SHARPNESS = 4
DIFF_THRESHOLD = 14  # Deghost 一致性选择色差阈值

ALGO_BASELINE = "baseline"
ALGO_DEGHOST = "deghost"
ALGO_LABELS = {
    ALGO_BASELINE: "BASELINE · 距离羽化",
    ALGO_DEGHOST: "DEGHOST · 去鬼影",
}


class StitchError(Exception):
    """拼接失败（配准不可靠 / 画布过大等），信息可直接展示给用户。"""


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


# ---------------- 基础图像工具 ----------------
def _ensure_bgr(img):
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _to_gray(img):
    if img.ndim == 3 and img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return img


def _h3(affine):
    """2x3 仿射矩阵补成 3x3 齐次矩阵。"""
    h = np.eye(3, dtype=np.float64)
    h[:2, :] = affine
    return h


def _crop_black(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 6, 255, cv2.THRESH_BINARY)
    pts = cv2.findNonZero(mask)
    if pts is None:
        return img.copy()
    x, y, w, h = cv2.boundingRect(pts)
    if w <= 0 or h <= 0:
        return img.copy()
    return img[y:y + h, x:x + w].copy()


# ---------------- SIFT 特征缓存 ----------------
class _Features:
    """按 (图号, 裁剪矩形) 缓存 CLAHE+SIFT 特征，避免重复计算。"""

    def __init__(self, imgs, grays):
        self.imgs = imgs
        self.grays = grays
        self._cache = {}
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._sift = cv2.SIFT_create(MAX_FEATURES, 3, 0.004, 10, 1.6)

    def get(self, idx, rect):
        key = (idx, rect)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        x, y, w, h = rect
        sub = self.imgs[idx][y:y + h, x:x + w]
        gray = _to_gray(sub)
        eq = self._clahe.apply(gray)
        kp, desc = self._sift.detectAndCompute(eq, None)
        self._cache[key] = (kp, desc)
        return kp, desc

    def reorder(self, order):
        """按新顺序重排图像，并同步迁移已缓存的描述子。"""
        pos = {old: new for new, old in enumerate(order)}
        self._cache = {(pos[i], rect): v for (i, rect), v in self._cache.items()
                       if i in pos}
        self.imgs = [self.imgs[i] for i in order]
        self.grays = [self.grays[i] for i in order]


# ---------------- 配准 ----------------
def _candidates(w, h):
    """全幅 + 右 40%/50%/60% 对左 40%/50%/60%，与 C# 候选区域一致。"""
    rects = [(0, 0, w, h, 0, 0, w, h)]
    for frac in (0.4, 0.5, 0.6):
        xa = int(w * (1 - frac))
        xb = int(w * frac)
        rects.append((xa, 0, w - xa, h, 0, 0, xb, h))
    return rects


def _refine_ecc(gray_a, gray_b, init_model):
    """ECC 欧氏变换亚像素精化；偏离初值过大时判为不可靠（返回 -1）。"""
    init = init_model.astype(np.float64)
    try:
        warp = init_model.astype(np.float32)
        mask = np.full(gray_b.shape, 255, dtype=np.uint8)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
        cc, warp = cv2.findTransformECC(
            gray_a.astype(np.float32), gray_b.astype(np.float32),
            warp, cv2.MOTION_EUCLIDEAN, criteria, mask, 5)
    except cv2.error:
        return -1.0, init

    dx, dy = float(warp[0, 2]), float(warp[1, 2])
    ang = np.degrees(np.arctan2(float(warp[1, 0]), float(warp[0, 0])))
    dx0, dy0 = float(init[0, 2]), float(init[1, 2])
    ang0 = np.degrees(np.arctan2(float(init[1, 0]), float(init[0, 0])))
    if (abs(dx - dx0) > MAX_ECC_SHIFT_PX
            or abs(dy - dy0) > MAX_ECC_SHIFT_PX
            or abs(ang - ang0) > MAX_ECC_ROT_DEG):
        return -1.0, init
    return float(cc), warp.astype(np.float64)


def _match_descriptors(desc_a, desc_b, ratio=RATIO, chunk=2048):
    """精确 2-NN 匹配 + Lowe ratio（等价 BFMatcher(knn=2) + ratio）。

    用 BLAS 分块计算全维平方欧氏距离：结果与 BFMatcher 一致（good 数相同），
    但不受 OpenCV 线程数影响，且比同线程数的 BFMatcher 更快。
    """
    if desc_a is None or desc_b is None:
        return []
    na, nb = len(desc_a), len(desc_b)
    if na < 2 or nb < 2:
        return []
    a = np.ascontiguousarray(desc_a, dtype=np.float32)
    b = np.ascontiguousarray(desc_b, dtype=np.float32)
    a_sq = np.einsum("ij,ij->i", a, a)
    b_sq = np.einsum("ij,ij->i", b, b)
    ratio_sq = ratio * ratio
    matched = []
    for start in range(0, na, chunk):
        qa = a[start:start + chunk]
        m = len(qa)
        # 平方欧氏距离（排序与欧氏距离单调等价，省一次开方）
        d2 = qa @ b.T
        d2 *= -2.0
        d2 += a_sq[start:start + chunk, None]
        d2 += b_sq[None, :]
        rows_all = np.arange(m)
        i1 = np.argmin(d2, axis=1)              # 最小距离的下标
        best1 = d2[rows_all, i1]
        d2[rows_all, i1] = np.inf              # 屏蔽最小值后再求次小
        i2 = np.argmin(d2, axis=1)
        best2 = d2[rows_all, i2]
        keep = best1 < ratio_sq * np.maximum(best2, 1e-12)
        rows = np.nonzero(keep)[0]
        if rows.size:
            matched.extend(zip((start + rows).tolist(), i1[rows].tolist()))
    return matched


def _match_pair(feat, idx_a, idx_b, sub=None, only_full=False, skip_ecc=False):
    """单对配准：多候选区域 SIFT+RANSAC+ECC，按 ECC 系数选优。

    返回 dict 或 None；model 满足 p_b = M · p_a。
    skip_ecc=True 时跳过 ECC（自动排序评分只需内点数，可大幅提速）。
    """
    sub = sub or (lambda frac, text: None)
    a = feat.imgs[idx_a]
    b = feat.imgs[idx_b]
    wa, ha = a.shape[1], a.shape[0]
    cands = _candidates(wa, ha)
    if only_full:
        cands = cands[:1]

    best = None
    total = len(cands)
    for ci, rects in enumerate(cands):
        sub(ci / total, f"候选区域 {ci + 1}/{total}")
        rect_a = rects[0:4]
        rect_b = rects[4:8]
        kp_a, desc_a = feat.get(idx_a, rect_a)
        kp_b, desc_b = feat.get(idx_b, rect_b)
        if desc_a is None or desc_b is None or len(kp_a) == 0 or len(kp_b) == 0:
            continue

        matched = _match_descriptors(desc_a, desc_b)
        if len(matched) < 12:
            continue

        src = [kp_a[qi].pt for qi, _ in matched]
        dst = [kp_b[ti].pt for _, ti in matched]

        src_m = np.float32(src).reshape(-1, 1, 2)
        dst_m = np.float32(dst).reshape(-1, 1, 2)
        model, inliers = cv2.estimateAffinePartial2D(
            src_m, dst_m, method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_THRESHOLD, maxIters=50000,
            confidence=0.9995, refineIters=10)
        if model is None or inliers is None:
            continue
        inlier_count = int(np.count_nonzero(inliers))
        if inlier_count < MIN_INLIERS:
            continue

        model = model.astype(np.float64)
        # 局部裁剪坐标折算回整幅图像
        model[0, 2] += rects[4] - rects[0]

        if skip_ecc:
            cc, refined = 1.0, model
        else:
            cc, refined = _refine_ecc(feat.grays[idx_a], feat.grays[idx_b], model)
        cand = {
            "model": refined, "cc": cc, "good": len(src),
            "inliers": inlier_count, "kp_a": len(kp_a), "kp_b": len(kp_b),
        }
        if best is None or cc > best["cc"]:
            best = cand

    # ECC 系数过低说明匹配多半来自重复纹理的伪对应，丢弃交给兜底
    if best is not None and best["cc"] < 0.1:
        return None
    return best


def _match_pair_by_ncc(gray_a, gray_b, allow_scale):
    """SIFT 退化时的灰度模板归一化互相关兜底；返回 p_b = M · p_a 的相似变换。"""
    wa, ha = gray_a.shape[1], gray_a.shape[0]
    wb, hb = gray_b.shape[1], gray_b.shape[0]
    if min(wa, ha, wb, hb) < 40:
        return None

    scales = ([0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2] if allow_scale
              else [0.95, 1.0, 1.05])
    best_ncc, best_scale, best_dx, best_dy = 0.0, 1.0, 0.0, 0.0

    for s in scales:
        aw, ah = int(round(wa * s)), int(round(ha * s))
        if aw < 40 or ah < 40:
            continue
        resized = cv2.resize(gray_a, (aw, ah), interpolation=cv2.INTER_AREA)
        tw = int(aw * 0.45)
        th = min(ah, hb)
        if tw < 30 or th < 30 or tw > wb:
            continue
        tx = aw - tw
        ty = (ah - th) // 2
        tmpl = resized[ty:ty + th, tx:tx + tw]
        if tmpl.shape[0] > hb or tmpl.shape[1] > wb:
            continue
        result = cv2.matchTemplate(gray_b, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val <= best_ncc:
            continue
        dx = max_loc[0] - tx
        dy = max_loc[1] - ty
        overlap_left = max(0.0, -dx / s)
        overlap_right = min(float(wa), (wb - dx) / s)
        if overlap_right - overlap_left < wa * 0.15:
            continue
        best_ncc, best_scale, best_dx, best_dy = max_val, s, dx, dy

    if best_ncc < 0.45:
        return None
    model = np.array([[best_scale, 0.0, best_dx],
                      [0.0, best_scale, best_dy]], dtype=np.float64)
    return {"model": model, "cc": best_ncc, "good": 0, "inliers": 0,
            "kp_a": 0, "kp_b": 0}


# ---------------- 自动排序（按重叠强度贪心串链） ----------------
def _auto_order(feat, n, report, lo, hi):
    pairs_total = n * (n - 1) // 2
    done = 0
    scores = np.full((n, n), -1.0)
    for i in range(n):
        for j in range(i + 1, n):
            best = _match_pair(feat, i, j, only_full=True, skip_ecc=True)
            s = float(best["inliers"]) if best else -1.0
            scores[i, j] = scores[j, i] = s
            done += 1
            report(lo + (hi - lo) * done / pairs_total,
                   f"自动排序：匹配 {i + 1}-{j + 1}")

    min_score = 8.0
    degree = [int(np.count_nonzero(scores[i] >= min_score)) for i in range(n)]
    start = min(range(n), key=lambda i: degree[i])
    if degree[start] == 0:
        raise StitchError("自动排序失败：任意两张图之间都找不到足够重叠")

    order, used = [start], {start}
    while len(order) < n:
        last = order[-1]
        nxt = max((i for i in range(n) if i not in used), key=lambda i: scores[last, i])
        if scores[last, nxt] >= min_score:
            order.append(nxt)
            used.add(nxt)
            continue
        first = order[0]
        nxt = max((i for i in range(n) if i not in used), key=lambda i: scores[first, i])
        if scores[first, nxt] >= min_score:
            order.insert(0, nxt)
            used.add(nxt)
            continue
        raise StitchError("自动排序失败：图片之间存在无法连接的部分，请改用手动排序")
    return order


# ---------------- 画布与融合 ----------------
def _canvas_geometry(imgs, transforms):
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for i, img in enumerate(imgs):
        h, w = img.shape[:2]
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        tc = cv2.perspectiveTransform(corners, transforms[i].astype(np.float64))
        tc = tc.reshape(-1, 2)
        min_x = min(min_x, float(tc[:, 0].min()))
        min_y = min(min_y, float(tc[:, 1].min()))
        max_x = max(max_x, float(tc[:, 0].max()))
        max_y = max(max_y, float(tc[:, 1].max()))

    canvas_w = int(np.ceil(max_x - np.floor(min_x)))
    canvas_h = int(np.ceil(max_y - np.floor(min_y)))
    if canvas_w <= 0 or canvas_h <= 0 or canvas_w * canvas_h > MAX_CANVAS_PIXELS:
        raise StitchError("拼接画布过大（超过 4 亿像素），已终止任务")

    shift = np.eye(3, dtype=np.float64)
    shift[0, 2] = -np.floor(min_x)
    shift[1, 2] = -np.floor(min_y)
    return canvas_w, canvas_h, shift


def _warp_to_canvas(img, matrix, size):
    """3x3 变换后的仿射 warp（取前两行）。"""
    m23 = matrix[:2, :].astype(np.float64)
    return cv2.warpAffine(img, m23, size, flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


def _blend_baseline(imgs, transforms, report, lo, hi):
    """按到有效区域边界的距离羽化加权（Baseline）。"""
    canvas_w, canvas_h, shift = _canvas_geometry(imgs, transforms)
    acc = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
    acc_w = np.zeros((canvas_h, canvas_w), dtype=np.float32)

    n = len(imgs)
    for i, img in enumerate(imgs):
        placed = _warp_to_canvas(img, shift @ transforms[i], (canvas_w, canvas_h))
        gray = cv2.cvtColor(placed, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY)
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3).astype(np.float32)
        weight = dist / max(RAMP, 1)
        np.clip(weight, None, 1.0, out=weight)
        if SHARPNESS > 1:
            weight = np.power(weight, float(SHARPNESS))
        acc += placed.astype(np.float32) * weight[..., None]
        acc_w += weight
        report(lo + (hi - lo) * (i + 1) / n, f"羽化融合 {i + 1}/{n}")

    acc_w += 1e-6
    pano = acc / acc_w[..., None]
    return np.clip(np.rint(pano), 0, 255).astype(np.uint8)


def _best_seam_dp(cost):
    """最佳缝合线 DP：逐行允许 ±1 列跳变，累计代价最小。

    父节点用二维表保存（仅存 ±1 偏移），避免一维父子表回溯串线。
    """
    h, w = cost.shape
    parent = np.zeros((h, w), dtype=np.int8)
    xs = np.arange(w)
    prev = cost[0].astype(np.float32, copy=True)
    inf = np.float32(np.inf)

    for y in range(1, h):
        left = np.empty(w, dtype=np.float32)
        left[0] = inf
        left[1:] = prev[:-1]
        right = np.empty(w, dtype=np.float32)
        right[-1] = inf
        right[:-1] = prev[1:]
        stack = np.stack([left, prev, right])          # 3 x W
        choice = np.argmin(stack, axis=0)              # 0/1/2
        parent[y] = (choice - 1).astype(np.int8)
        prev = cost[y] + stack[choice, xs]
        del left, right, stack

    seam = np.empty(h, dtype=np.int32)
    seam[h - 1] = int(np.argmin(prev))
    for y in range(h - 2, -1, -1):
        seam[y] = seam[y + 1] + int(parent[y + 1, seam[y + 1]])
    return seam


def _estimate_gain(prev, cur, overlap):
    """重叠区逐通道均值比，clamp 到 [0.6, 1.7]。"""
    gains = [1.0, 1.0, 1.0]
    sel = overlap > 0
    if int(np.count_nonzero(sel)) < 50:
        return gains
    for c in range(3):
        pm = float(prev[..., c][sel].mean())
        cm = float(cur[..., c][sel].mean())
        g = 1.0 if cm < 1e-3 else pm / cm
        gains[c] = min(1.7, max(0.6, g))
    return gains


def _fuse_pair_deghost(prev, prev_mask, cur, cur_mask, feather):
    """增益匹配 + 最佳缝合线 + smoothstep 过渡 + 色差一致性选择。"""
    h, w = prev.shape[:2]
    overlap = cv2.bitwise_and(prev_mask, cur_mask)
    if cv2.countNonZero(overlap) < 100:
        out = prev.copy()
        cv2.copyTo(cur, cur_mask, out)
        return out

    gains = _estimate_gain(prev, cur, overlap)
    cur_gain = cur.astype(np.float32)
    for c, g in enumerate(gains):
        if abs(g - 1.0) > 1e-3:
            cur_gain[..., c] *= np.float32(g)
    cur_gain = np.clip(np.rint(cur_gain), 0, 255).astype(np.uint8)

    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(cur_gain, cv2.COLOR_BGR2GRAY)
    color_diff_u8 = cv2.absdiff(prev_gray, cur_gray)

    color_cost = color_diff_u8.astype(np.float32)
    gx1 = cv2.Sobel(prev_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy1 = cv2.Sobel(prev_gray, cv2.CV_32F, 0, 1, ksize=3)
    gx2 = cv2.Sobel(cur_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy2 = cv2.Sobel(cur_gray, cv2.CV_32F, 0, 1, ksize=3)
    cost = color_cost + 0.5 * (np.abs(gx1 - gx2) + np.abs(gy1 - gy2))
    del color_cost, gx1, gy1, gx2, gy2
    cost[overlap == 0] = 1e6  # 强制缝合线留在重叠区

    seam = _best_seam_dp(cost)
    del cost

    in_ov = overlap > 0
    xs = np.arange(w, dtype=np.int32)[None, :]
    dist = np.abs(xs - seam[:, None])

    if feather > 0:
        weight_cur = 0.5 * (1.0 - np.cos(np.pi * np.minimum(dist, feather) / feather))
    else:
        weight_cur = np.zeros((h, w), dtype=np.float32)

    w_prev = 1.0 - weight_cur
    w_cur = weight_cur
    # 色差过大时不做平均，直接取较近的一侧，避免鬼影
    big = (color_diff_u8 > DIFF_THRESHOLD) & in_ov
    near = big & (dist <= feather / 2.0)
    far = big & (dist > feather / 2.0)
    w_prev = np.where(near, 1.0, w_prev)
    w_cur = np.where(near, 0.0, w_cur)
    w_prev = np.where(far, 0.0, w_prev)
    w_cur = np.where(far, 1.0, w_cur)

    prev_f = prev.astype(np.float32)
    cur_f = cur_gain.astype(np.float32)
    blended = prev_f * w_prev[..., None] + cur_f * w_cur[..., None]
    out = np.where(in_ov[..., None], blended, prev_f)
    out = np.where((~in_ov & (cur_mask > 0))[..., None], cur_f, out)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _fuse_pair_feather(prev, prev_mask, cur, cur_mask, ramp):
    """Deghost 失败时的两图简单羽化兜底。"""
    def weight_of(mask):
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3).astype(np.float32)
        w = dist / max(ramp, 1)
        np.clip(w, None, 1.0, out=w)
        return w

    w_prev = weight_of(prev_mask)
    w_cur = weight_of(cur_mask)
    s = w_prev + w_cur + 1e-6
    w_prev /= s
    w_cur /= s
    out = (prev.astype(np.float32) * w_prev[..., None]
           + cur.astype(np.float32) * w_cur[..., None])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _blend_deghost(imgs, transforms, report, lo, hi):
    """顺序式去鬼影融合：逐对执行 Deghost 融合。"""
    feather = max(RAMP, 10)
    canvas_w, canvas_h, shift = _canvas_geometry(imgs, transforms)
    size = (canvas_w, canvas_h)
    n = len(imgs)

    placed_list, mask_list = [], []
    warp_hi = lo + (hi - lo) * 0.35
    for i, img in enumerate(imgs):
        placed = _warp_to_canvas(img, shift @ transforms[i], size)
        gray = cv2.cvtColor(placed, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY)
        placed_list.append(placed)
        mask_list.append(mask)
        report(lo + (warp_hi - lo) * (i + 1) / n, f"投影到画布 {i + 1}/{n}")

    prev = placed_list[0].copy()
    prev_mask = mask_list[0].copy()
    for i in range(1, n):
        fused = _fuse_pair_deghost(prev, prev_mask, placed_list[i], mask_list[i], feather)
        if fused is None:
            fused = _fuse_pair_feather(prev, prev_mask, placed_list[i],
                                       mask_list[i], feather)
        prev = fused
        prev_mask = cv2.bitwise_or(prev_mask, mask_list[i])
        report(warp_hi + (hi - warp_hi) * i / (n - 1), f"去鬼影融合 {i}/{n - 1}")
    return prev


# ---------------- 主入口 ----------------
def stitch(images, algorithm=ALGO_BASELINE, auto_order=False, progress=None):
    """按 C# PanoramaStitcher 的算法拼接图像。

    :param images: BGR 图像列表（顺序即手动顺序）
    :param algorithm: ALGO_BASELINE / ALGO_DEGHOST
    :param auto_order: True 时先按重叠强度自动定序
    :param progress: progress(fraction, text)，fraction 0..1
    :return: 全景图 BGR uint8
    """
    report = progress or (lambda frac, text: None)
    n = len(images)
    if n < 2:
        raise StitchError("至少需要 2 张图片才能融合")

    report(0.0, "缩放预处理…")
    scaled, grays = [], []
    for i, img in enumerate(images):
        bgr = _ensure_bgr(img)
        if abs(SCALE - 1.0) < 1e-6:
            small = bgr.copy()
        else:
            small = cv2.resize(bgr, None, fx=SCALE, fy=SCALE,
                               interpolation=cv2.INTER_AREA)
        scaled.append(small)
        grays.append(_to_gray(small))
        report(0.05 * (i + 1) / n, "缩放预处理…")

    feat = _Features(scaled, grays)

    if auto_order:
        order = _auto_order(feat, n, report, 0.05, 0.30)
        feat.reorder(order)
        scaled, grays = feat.imgs, feat.grays
        reg_lo = 0.30
    else:
        reg_lo = 0.05

    reg_hi = 0.72
    transforms = [np.eye(3, dtype=np.float64)]
    pairs = n - 1
    for k in range(1, n):
        span = (reg_hi - reg_lo) / pairs
        base = reg_lo + (k - 1) * span

        def sub(frac, text, base=base, span=span, k=k):
            report(base + span * frac, f"配准第 {k}/{pairs} 对 · {text}")

        best = _match_pair(feat, k - 1, k, sub)
        if best is None:
            sub(0.9, "SIFT 退化，灰度模板兜底")
            best = _match_pair_by_ncc(grays[k - 1], grays[k], allow_scale=False)
        if best is None:
            raise StitchError(
                f"第 {k} 张与第 {k + 1} 张配准失败：相邻画面重叠不足，"
                "请调整顺序或改用自动排序")

        inv = cv2.invertAffineTransform(best["model"])
        transforms.append(transforms[-1] @ _h3(inv))

    if algorithm == ALGO_DEGHOST:
        pano = _blend_deghost(scaled, transforms, report, reg_hi, 0.97)
    else:
        pano = _blend_baseline(scaled, transforms, report, reg_hi, 0.97)

    report(0.98, "裁剪黑边…")
    pano = _crop_black(pano)
    report(1.0, "融合完成")
    return pano
