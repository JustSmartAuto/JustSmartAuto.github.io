using System;
using System.Collections.Generic;
using OpenCvSharp;
using OpenCvSharp.Features2D;

namespace JustSmart.Panorama
{
    /// <summary>
    /// 相邻图像对的配准信息
    /// </summary>
    public class PanoramaPairInfo
    {
        /// <summary>左图名称</summary>
        public string FromCamera { get; set; }

        /// <summary>右图名称</summary>
        public string ToCamera { get; set; }

        /// <summary>左图关键点数</summary>
        public int KeypointsA { get; set; }

        /// <summary>右图关键点数</summary>
        public int KeypointsB { get; set; }

        /// <summary>ratio 筛选后的粗匹配数</summary>
        public int Good { get; set; }

        /// <summary>RANSAC 内点数</summary>
        public int Inliers { get; set; }

        /// <summary>ECC 相关系数</summary>
        public double EccCc { get; set; }

        /// <summary>水平位移（像素，处理尺度下）</summary>
        public double Dx { get; set; }

        /// <summary>垂直位移（像素，处理尺度下）</summary>
        public double Dy { get; set; }

        /// <summary>相对旋转角（度）</summary>
        public double RotationDeg { get; set; }
    }

    /// <summary>
    /// 多相机图像 SIFT 特征匹配拼接。
    /// 算法与现场验证脚本 stitch_topcam.py 一致：
    /// SIFT + 2-NN ratio -> RANSAC 相似变换 -> ECC 精化 -> 变换链累积 -> 距离变换羽化融合。
    /// </summary>
    public static class PanoramaStitcher
    {
        private const double Ratio = 0.75;
        private const double RansacThreshold = 4.0;
        private const int MinInliers = 20;
        private const int MaxFeatures = 20000;

        /// <summary>ECC 精化相对 SIFT 初值允许的最大平移偏差（像素，处理尺度下）</summary>
        private const double MaxEccShiftPx = 150;

        /// <summary>
        /// 按给定顺序拼接图像。失败返回 false，不抛异常。
        /// </summary>
        /// <param name="images">按物理从左到右排列的图像</param>
        /// <param name="names">与图像一一对应的名称（用于日志）</param>
        /// <param name="panorama">输出全景图（CV_8UC3）</param>
        /// <param name="pairs">相邻对的配准信息</param>
        /// <param name="scale">处理尺度，默认 0.5</param>
        /// <param name="ramp">羽化宽度（像素）</param>
        /// <param name="sharpness">羽化权重指数</param>
        public static bool TryStitch(
            IList<Mat> images,
            IList<string> names,
            out Mat panorama,
            out List<PanoramaPairInfo> pairs,
            double scale = 0.5,
            int ramp = 80,
            int sharpness = 4,
            bool allowScaleFallback = false,
            PanoramaAlgorithmId algorithm = PanoramaAlgorithmId.Baseline
        )
        {
            panorama = null;
            pairs = new List<PanoramaPairInfo>();
            if (images == null || images.Count < 2)
            {
                return false;
            }

            var scaled = new List<Mat>();
            var grays = new List<Mat>();
            var transforms = new List<Mat>();
            try
            {
                for (int i = 0; i < images.Count; i++)
                {
                    if (images[i] == null || images[i].Empty())
                    {
                        return false;
                    }
                    Mat img;
                    if (Math.Abs(scale - 1.0) < 1e-6)
                    {
                        img = images[i].Clone();
                    }
                    else
                    {
                        img = new Mat();
                        Cv2.Resize(
                            images[i],
                            img,
                            new Size(),
                            scale,
                            scale,
                            InterpolationFlags.Area
                        );
                    }
                    scaled.Add(EnsureBgr(img));
                    if (scaled[scaled.Count - 1] != img)
                    {
                        img.Dispose();
                    }
                    grays.Add(ToGray(scaled[scaled.Count - 1]));
                }

                transforms.Add(Identity3());
                for (int k = 1; k < scaled.Count; k++)
                {
                    Candidate best = DropUnreliable(
                        MatchPair(scaled[k - 1], scaled[k], grays[k - 1], grays[k])
                    );
                    if (best == null)
                    {
                        // 侧相机视野里排布着大量重复工装模块，SIFT 会退化；退化为灰度模板相关搜索
                        best = MatchPairByNcc(
                            grays[k - 1],
                            grays[k],
                            allowScaleFallback && k == 1
                        );
                    }
                    if (best == null)
                    {
                        return false;
                    }
                    using (best.Model)
                    {
                        using (
                            Mat inverted = new Mat()
                        )
                        {
                            Cv2.InvertAffineTransform(best.Model, inverted);
                            Mat warp33 = ToHomography(inverted);
                            transforms.Add(Mul3(transforms[transforms.Count - 1], warp33));
                            warp33.Dispose();
                        }

                        PanoramaPairInfo info = new PanoramaPairInfo
                        {
                            FromCamera = names != null && k - 1 < names.Count ? names[k - 1] : "",
                            ToCamera = names != null && k < names.Count ? names[k] : "",
                            KeypointsA = best.KeypointsA,
                            KeypointsB = best.KeypointsB,
                            Good = best.Good,
                            Inliers = best.Inliers,
                            EccCc = best.EccCc,
                            Dx = best.Model.At<double>(0, 2),
                            Dy = best.Model.At<double>(1, 2),
                            RotationDeg =
                                Math.Atan2(best.Model.At<double>(1, 0), best.Model.At<double>(0, 0))
                                * 180.0
                                / Math.PI
                        };
                        pairs.Add(info);
                    }
                }

                panorama = algorithm == PanoramaAlgorithmId.Deghost
                    ? BlendDeghostSequential(scaled, transforms, ramp)
                    : Blend(scaled, transforms, ramp, sharpness);
                return panorama != null;
            }
            catch (Exception)
            {
                panorama?.Dispose();
                panorama = null;
                return false;
            }
            finally
            {
                foreach (Mat m in scaled)
                {
                    m.Dispose();
                }
                foreach (Mat g in grays)
                {
                    g.Dispose();
                }
                foreach (Mat t in transforms)
                {
                    t.Dispose();
                }
            }
        }

        /// <summary>
        /// 单对图像配准：多候选区域 + RANSAC + ECC，按 ECC 相关系数选优。
        /// 返回的模型满足 p_b = M · p_a。
        /// </summary>
        private static Candidate MatchPair(Mat a, Mat b, Mat grayA, Mat grayB)
        {
            int wa = a.Cols;
            int wb = b.Cols;
            var candidates = new List<Rect[]>
            {
                new[] { new Rect(0, 0, wa, a.Rows), new Rect(0, 0, wb, b.Rows) }
            };
            double[] fracs = { 0.4, 0.5, 0.6 };
            foreach (double frac in fracs)
            {
                int xa = (int)(wa * (1 - frac));
                int xb = (int)(wb * frac);
                candidates.Add(
                    new[] { new Rect(xa, 0, wa - xa, a.Rows), new Rect(0, 0, xb, b.Rows) }
                );
            }

            Candidate best = null;
            foreach (Rect[] rects in candidates)
            {
                Mat subA = new Mat(a, rects[0]);
                Mat subB = new Mat(b, rects[1]);
                KeyPoint[] kpA;
                KeyPoint[] kpB;
                Mat descA = null;
                Mat descB = null;
                Mat model = null;
                try
                {
                    DetectSift(subA, out kpA, out descA);
                    DetectSift(subB, out kpB, out descB);
                    if (descA == null || descB == null || descA.Empty() || descB.Empty()
                        || kpA.Length == 0 || kpB.Length == 0)
                    {
                        continue;
                    }

                    var srcList = new List<Point2f>();
                    var dstList = new List<Point2f>();
                    using (BFMatcher matcher = new BFMatcher(NormTypes.L2))
                    {
                        DMatch[][] knn = matcher.KnnMatch(descA, descB, 2);
                        foreach (DMatch[] pair in knn)
                        {
                            if (pair == null || pair.Length < 2)
                            {
                                continue;
                            }
                            if (pair[0].Distance < Ratio * pair[1].Distance)
                            {
                                srcList.Add(kpA[pair[0].QueryIdx].Pt);
                                dstList.Add(kpB[pair[0].TrainIdx].Pt);
                            }
                        }
                    }
                    if (srcList.Count < 12)
                    {
                        continue;
                    }

                    Mat srcMat = PointsToMat(srcList);
                    Mat dstMat = PointsToMat(dstList);
                    Mat inliers = new Mat();
                    model = Cv2.EstimateAffinePartial2D(
                        srcMat,
                        dstMat,
                        inliers,
                        RobustEstimationAlgorithms.RANSAC,
                        RansacThreshold,
                        50000,
                        0.9995,
                        10
                    );
                    srcMat.Dispose();
                    dstMat.Dispose();
                    if (model == null || inliers == null || inliers.Empty())
                    {
                        inliers?.Dispose();
                        continue;
                    }
                    int inlierCount = Cv2.CountNonZero(inliers);
                    inliers.Dispose();
                    if (inlierCount < MinInliers)
                    {
                        continue;
                    }

                    // 局部区域坐标折算回整幅图像
                    model.Set<double>(0, 2, model.At<double>(0, 2) + rects[1].X - rects[0].X);

                    Mat refined;
                    double cc = RefineEcc(grayA, grayB, model, out refined);
                    model.Dispose();
                    model = refined;

                    Candidate cand = new Candidate
                    {
                        Model = model,
                        EccCc = cc,
                        Good = srcList.Count,
                        Inliers = inlierCount,
                        KeypointsA = kpA.Length,
                        KeypointsB = kpB.Length
                    };
                    if (best == null || cand.EccCc > best.EccCc)
                    {
                        best?.Dispose();
                        best = cand;
                        model = null; // 所有权移交给 best
                    }
                }
                finally
                {
                    subA.Dispose();
                    subB.Dispose();
                    model?.Dispose();
                    (descA as IDisposable)?.Dispose();
                    (descB as IDisposable)?.Dispose();
                }
            }
            return best;
        }

        /// <summary>
        /// ECC 精化失败（cc &lt; 0.1）说明 SIFT 匹配多半来自重复纹理的伪对应，
        /// 丢弃该解，交给灰度模板兜底，避免拼出明显错位的全景图。
        /// </summary>
        private static Candidate DropUnreliable(Candidate best)
        {
            if (best == null)
            {
                return null;
            }
            if (best.EccCc >= 0.1)
            {
                return best;
            }
            best.Model?.Dispose();
            return null;
        }

        /// <summary>
        /// SIFT 配准失败时的兜底：灰度模板归一化互相关搜索。
        /// 侧相机画面被重复的工装模块占据，特征点匹配容易退化，但整幅灰度分布仍可定位；
        /// allowScale 为 true 时同时搜索尺度，用于兼容斜上方、视野更近的 SideBCam1。
        /// 返回的模型为相似变换：p_b = M · p_a。
        /// </summary>
        private static Candidate MatchPairByNcc(Mat grayA, Mat grayB, bool allowScale)
        {
            try
            {
                int wa = grayA.Cols;
                int ha = grayA.Rows;
                int wb = grayB.Cols;
                int hb = grayB.Rows;
                if (wa < 40 || ha < 40 || wb < 40 || hb < 40)
                {
                    return null;
                }

                double[] scales = allowScale
                    ? new[] { 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2 }
                    : new[] { 0.95, 1.0, 1.05 };

                double bestNcc = 0;
                double bestScale = 1.0;
                double bestDx = 0;
                double bestDy = 0;

                foreach (double s in scales)
                {
                    int aw = (int)Math.Round(wa * s);
                    int ah = (int)Math.Round(ha * s);
                    if (aw < 40 || ah < 40)
                    {
                        continue;
                    }
                    using (Mat resized = new Mat())
                    {
                        Cv2.Resize(
                            grayA,
                            resized,
                            new Size(aw, ah),
                            0,
                            0,
                            InterpolationFlags.Area
                        );
                        int tw = (int)(aw * 0.45);
                        int th = Math.Min(ah, hb);
                        if (tw < 30 || th < 30 || tw > wb)
                        {
                            continue;
                        }
                        int tx = aw - tw;
                        int ty = (ah - th) / 2;
                        using (Mat tmpl = new Mat(resized, new Rect(tx, ty, tw, th)))
                        using (Mat result = new Mat())
                        {
                            Cv2.MatchTemplate(
                                grayB,
                                tmpl,
                                result,
                                TemplateMatchModes.CCoeffNormed
                            );
                            Cv2.MinMaxLoc(
                                result,
                                out _,
                                out double maxVal,
                                out _,
                                out Point maxLoc
                            );
                            if (maxVal <= bestNcc)
                            {
                                continue;
                            }
                            double dx = maxLoc.X - tx;
                            double dy = maxLoc.Y - ty;
                            // 重叠区必须足够大，否则重复纹理很容易给出伪峰
                            double overlapLeft = Math.Max(0, -dx / s);
                            double overlapRight = Math.Min(wa, (wb - dx) / s);
                            if (overlapRight - overlapLeft < wa * 0.15)
                            {
                                continue;
                            }
                            bestNcc = maxVal;
                            bestScale = s;
                            bestDx = dx;
                            bestDy = dy;
                        }
                    }
                }

                // 侧相机画面纹理弱，取 0.45 作为可接受相关性下限
                if (bestNcc < 0.45)
                {
                    return null;
                }

                Mat model = new Mat(2, 3, MatType.CV_64FC1, Scalar.All(0));
                model.Set(0, 0, bestScale);
                model.Set(1, 1, bestScale);
                model.Set(0, 2, bestDx);
                model.Set(1, 2, bestDy);
                PanoramaProtocol.Trace(
                    "配准兜底: ncc="
                        + bestNcc.ToString("0.###")
                        + " scale="
                        + bestScale.ToString("0.##")
                        + " dx="
                        + bestDx.ToString("0.#")
                        + " dy="
                        + bestDy.ToString("0.#")
                );
                return new Candidate
                {
                    Model = model,
                    EccCc = bestNcc,
                    Good = 0,
                    Inliers = 0,
                    KeypointsA = 0,
                    KeypointsB = 0
                };
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("配准兜底异常: " + ex.Message);
                return null;
            }
        }

        /// <summary>
        /// ECC 欧氏变换亚像素精化。偏离初值过大时保留初值。
        /// </summary>
        private static double RefineEcc(Mat grayA, Mat grayB, Mat initModel, out Mat refined)
        {
            refined = initModel.Clone();
            try
            {
                using (Mat fa = new Mat())
                using (Mat fb = new Mat())
                using (Mat mask = new Mat(grayB.Size(), MatType.CV_8UC1, Scalar.All(255)))
                using (Mat warp = new Mat())
                {
                    grayA.ConvertTo(fa, MatType.CV_32F);
                    grayB.ConvertTo(fb, MatType.CV_32F);
                    initModel.ConvertTo(warp, MatType.CV_32F);
                    TermCriteria criteria = new TermCriteria(
                        CriteriaTypes.Eps | CriteriaTypes.Count,
                        100,
                        1e-6
                    );
                    double cc = Cv2.FindTransformECC(
                        fa,
                        fb,
                        warp,
                        MotionTypes.Euclidean,
                        criteria,
                        mask,
                        5
                    );
                    double dx = warp.At<float>(0, 2);
                    double dy = warp.At<float>(1, 2);
                    double ang =
                        Math.Atan2(warp.At<float>(1, 0), warp.At<float>(0, 0)) * 180.0 / Math.PI;
                    double dx0 = initModel.At<double>(0, 2);
                    double dy0 = initModel.At<double>(1, 2);
                    double ang0 =
                        Math.Atan2(initModel.At<double>(1, 0), initModel.At<double>(0, 0))
                        * 180.0
                        / Math.PI;
                    // 重复纹理可能把 ECC 拉偏，偏离初值过多时不采用
                    if (
                        Math.Abs(dx - dx0) > MaxEccShiftPx
                        || Math.Abs(dy - dy0) > MaxEccShiftPx
                        || Math.Abs(ang - ang0) > 5.0
                    )
                    {
                        return -1.0;
                    }
                    refined.Dispose();
                    refined = new Mat();
                    warp.ConvertTo(refined, MatType.CV_64F);
                    return cc;
                }
            }
            catch (OpenCVException ex)
            {
                System.Diagnostics.Debug.WriteLine("[RefineEcc] " + ex.Message);
                return -1.0;
            }
        }

        /// <summary>
        /// 把各图按变换链贴到同一画布，按到有效区域边界的距离羽化加权。
        /// </summary>
        private static Mat Blend(
            List<Mat> images,
            List<Mat> transforms,
            int ramp,
            int sharpness
        )
        {
            double minX = double.MaxValue;
            double minY = double.MaxValue;
            double maxX = double.MinValue;
            double maxY = double.MinValue;
            for (int i = 0; i < images.Count; i++)
            {
                Mat t = transforms[i];
                int h = images[i].Rows;
                int w = images[i].Cols;
                Point2d[] corners = new Point2d[]
                {
                    new Point2d(0, 0),
                    new Point2d(w, 0),
                    new Point2d(w, h),
                    new Point2d(0, h)
                };
                foreach (Point2d p in corners)
                {
                    double x = t.At<double>(0, 0) * p.X + t.At<double>(0, 1) * p.Y + t.At<double>(0, 2);
                    double y = t.At<double>(1, 0) * p.X + t.At<double>(1, 1) * p.Y + t.At<double>(1, 2);
                    minX = Math.Min(minX, x);
                    minY = Math.Min(minY, y);
                    maxX = Math.Max(maxX, x);
                    maxY = Math.Max(maxY, y);
                }
            }

            int canvasW = (int)Math.Ceiling(maxX - Math.Floor(minX));
            int canvasH = (int)Math.Ceiling(maxY - Math.Floor(minY));
            if (canvasW <= 0 || canvasH <= 0 || (long)canvasW * canvasH > 400000000L)
            {
                return null;
            }

            Mat shift = Identity3();
            shift.Set<double>(0, 2, -Math.Floor(minX));
            shift.Set<double>(1, 2, -Math.Floor(minY));

            Mat acc = new Mat(canvasH, canvasW, MatType.CV_32FC3, Scalar.All(0));
            Mat accW = new Mat(canvasH, canvasW, MatType.CV_32FC1, Scalar.All(0));
            try
            {
                for (int i = 0; i < images.Count; i++)
                {
                    Mat placed = new Mat();
                    Mat placedF = new Mat();
                    Mat grayPlaced = new Mat();
                    Mat dist = new Mat();
                    Mat weight = new Mat();
                    Mat weight3 = new Mat();
                    Mat prod = new Mat();
                    Mat full = Mul3(shift, transforms[i]);
                    try
                    {
                        Mat m23;
                        using (Mat roi = new Mat(full, new Rect(0, 0, 3, 2)))
                        {
                            m23 = roi.Clone();
                        }
                        using (m23)
                        {
                            Cv2.WarpAffine(
                                images[i],
                                placed,
                                m23,
                                new Size(canvasW, canvasH),
                                InterpolationFlags.Linear,
                                BorderTypes.Constant,
                                Scalar.All(0)
                            );
                        }

                        Cv2.CvtColor(placed, grayPlaced, ColorConversionCodes.BGR2GRAY);
                        Cv2.Threshold(grayPlaced, grayPlaced, 0, 255, ThresholdTypes.Binary);
                        Cv2.DistanceTransform(
                            grayPlaced,
                            dist,
                            DistanceTypes.L2,
                            DistanceTransformMasks.Mask3
                        );
                        dist.ConvertTo(weight, MatType.CV_32F, 1.0 / Math.Max(ramp, 1));
                        Cv2.Threshold(weight, weight, 1.0, 1.0, ThresholdTypes.Trunc);
                        for (int s = 1; s < Math.Max(sharpness, 1); s++)
                        {
                            weight = weight.Mul(weight);
                        }
                        Cv2.Merge(new[] { weight, weight, weight }, weight3);

                        placed.ConvertTo(placedF, MatType.CV_32FC3);
                        Cv2.Multiply(placedF, weight3, prod);
                        Cv2.Add(acc, prod, acc);
                        Cv2.Add(accW, weight, accW);
                    }
                    finally
                    {
                        placed.Dispose();
                        placedF.Dispose();
                        grayPlaced.Dispose();
                        dist.Dispose();
                        weight.Dispose();
                        weight3.Dispose();
                        prod.Dispose();
                        full?.Dispose();
                    }
                }

                Cv2.Add(accW, Scalar.All(1e-6), accW);
                Mat accW3 = new Mat();
                Mat panoF = new Mat();
                Mat pano8u = new Mat();
                try
                {
                    Cv2.Merge(new[] { accW, accW, accW }, accW3);
                    Cv2.Divide(acc, accW3, panoF);
                    panoF.ConvertTo(pano8u, MatType.CV_8UC3);
                    return CropBlack(pano8u);
                }
                finally
                {
                    accW3.Dispose();
                    panoF.Dispose();
                    pano8u.Dispose();
                }
            }
            finally
            {
                acc.Dispose();
                accW.Dispose();
                shift.Dispose();
            }
        }

// ============================================================================
// 去鬼影融合（论文：瞿中等 2015）
//   增益匹配 → 最佳缝合线 DP → 限定区 smoothstep 融合 → 一致性选择
// ============================================================================

/// <summary>
/// 顺序式去鬼影融合：逐对融合，每对执行增益匹配 + 最佳缝合线 DP + 限定区 smoothstep 融合。
/// 配准（images/transforms 已由上层 SIFT+RANSAC+ECC 算好）；此方法专注融合。
/// </summary>
private static Mat BlendDeghostSequential(
    List<Mat> images,
    List<Mat> transforms,
    int feather)
{
    if (images == null || images.Count == 0) return null;
    feather = Math.Max(feather, 10);

    // 1. 计算画布边界（与 Blend 同逻辑）
    double minX = double.MaxValue, minY = double.MaxValue;
    double maxX = double.MinValue, maxY = double.MinValue;
    for (int i = 0; i < images.Count; i++)
    {
        Mat t = transforms[i];
        int h = images[i].Rows, w = images[i].Cols;
        Point2d[] corners = new[]
        {
            new Point2d(0, 0), new Point2d(w, 0), new Point2d(w, h), new Point2d(0, h)
        };
        foreach (Point2d p in corners)
        {
            double x = t.At<double>(0, 0) * p.X + t.At<double>(0, 1) * p.Y + t.At<double>(0, 2);
            double y = t.At<double>(1, 0) * p.X + t.At<double>(1, 1) * p.Y + t.At<double>(1, 2);
            if (x < minX) minX = x; if (y < minY) minY = y;
            if (x > maxX) maxX = x; if (y > maxY) maxY = y;
        }
    }
    int canvasW = (int)Math.Ceiling(maxX - Math.Floor(minX));
    int canvasH = (int)Math.Ceiling(maxY - Math.Floor(minY));
    if (canvasW <= 0 || canvasH <= 0 || (long)canvasW * canvasH > 400_000_000L)
        return null;

    Mat shift = Identity3();
    shift.Set(0, 2, -Math.Floor(minX));
    shift.Set(1, 2, -Math.Floor(minY));

    try
    {
        // 2. 把所有图 warp 到画布坐标 + 生成有效 mask
        var placedList = new List<Mat>();
        var maskList = new List<Mat>();
        try
        {
            for (int i = 0; i < images.Count; i++)
            {
                Mat placed = new Mat();
                Mat mask = new Mat();
                Mat full = Mul3(shift, transforms[i]);
                try
                {
                    Mat m23;
                    using (Mat roi = new Mat(full, new Rect(0, 0, 3, 2)))
                    {
                        m23 = roi.Clone();
                    }
                    try
                    {
                        using (m23)
                        {
                            Cv2.WarpAffine(images[i], placed, m23,
                                new Size(canvasW, canvasH),
                                InterpolationFlags.Linear, BorderTypes.Constant, Scalar.All(0));
                        }
                        // mask: 非零像素为 255
                        Cv2.CvtColor(placed, mask, ColorConversionCodes.BGR2GRAY);
                        Cv2.Threshold(mask, mask, 0, 255, ThresholdTypes.Binary);
                    }
                    finally { m23?.Dispose(); }
                }
                finally { full.Dispose(); }
                placedList.Add(placed);
                maskList.Add(mask);
            }

            // 3. 顺序融合：prev = placed[0]，依次跟 placed[i] 融合
            Mat prev = placedList[0].Clone();
            Mat prevMask = maskList[0].Clone();
            try
            {
                for (int i = 1; i < placedList.Count; i++)
                {
                    Mat cur = placedList[i];
                    Mat curMask = maskList[i];
                    Mat fused = FusePairDeghost(prev, prevMask, cur, curMask, feather);
                    if (fused == null)
                    {
                        // Deghost 失败（极端情况），退化为简单羽化
                        PanoramaProtocol.Trace("Deghost 配对 " + i + " 失败，退化为 Baseline 融合");
                        fused = FusePairFeather(prev, prevMask, cur, curMask, feather);
                        if (fused == null) return null;
                    }
                    prev.Dispose();
                    prev = fused;
                    // 融合后的 mask = 两者的并集（简化：直接用 curMask 覆盖 prevMask 上无效区域）
                    Mat newMask = new Mat();
                    Cv2.BitwiseOr(prevMask, curMask, newMask);
                    prevMask.Dispose();
                    prevMask = newMask;
                }
                return CropBlack(prev);
            }
            finally
            {
                prev?.Dispose();
                prevMask?.Dispose();
            }
        }
        finally
        {
            foreach (Mat m in placedList) m.Dispose();
            foreach (Mat m in maskList) m.Dispose();
        }
    }
    catch (Exception ex)
    {
        PanoramaProtocol.Trace("BlendDeghostSequential 异常: " + ex.Message);
        return null;
    }
    finally
    {
        shift.Dispose();
    }
}

/// <summary>
/// Deghost 配对融合：增益匹配 + 最佳缝合线 DP + smoothstep + 一致性选择。
/// </summary>
private static Mat FusePairDeghost(
    Mat prev, Mat prevMask,
    Mat cur, Mat curMask,
    int feather)
{
    int H = prev.Rows, W = prev.Cols;
    Mat overlapMask = new Mat();
    Cv2.BitwiseAnd(prevMask, curMask, overlapMask);
    if (Cv2.CountNonZero(overlapMask) < 100)
    {
        // 几乎无重叠，直接 cur 覆盖 prev（curMask 区域）
        Mat result = prev.Clone();
        cur.CopyTo(result, curMask);
        overlapMask.Dispose();
        return result;
    }

    try
    {
        // 1. 增益匹配（逐通道，重叠区内中值比）
        double[] gain = EstimateGain(prev, cur, overlapMask);
        Mat curGain = cur.Clone();
        for (int c = 0; c < 3; c++)
        {
            double g = Math.Max(0.6, Math.Min(1.7, gain[c]));
            if (Math.Abs(g - 1.0) > 1e-3)
            {
                Mat ch = new Mat();
                Cv2.ExtractChannel(curGain, ch, c);
                ch.ConvertTo(ch, MatType.CV_32FC1);
                Cv2.Multiply(ch, g, ch);
                ch.ConvertTo(ch, MatType.CV_8UC1);
                Cv2.InsertChannel(ch, curGain, c);
                ch.Dispose();
            }
        }

        // 2. 代价图（颜色差 + 0.5 * Sobel 梯度差）
        Mat prevGray = new Mat(); Mat curGray = new Mat();
        Cv2.CvtColor(prev, prevGray, ColorConversionCodes.BGR2GRAY);
        Cv2.CvtColor(curGain, curGray, ColorConversionCodes.BGR2GRAY);

        Mat colorDiff = new Mat();
        Cv2.Absdiff(prevGray, curGray, colorDiff);
        colorDiff.ConvertTo(colorDiff, MatType.CV_32F);

        Mat gx1 = new Mat(), gy1 = new Mat();
        Mat gx2 = new Mat(), gy2 = new Mat();
        Cv2.Sobel(prevGray, gx1, MatType.CV_32F, 1, 0, 3);
        Cv2.Sobel(prevGray, gy1, MatType.CV_32F, 0, 1, 3);
        Cv2.Sobel(curGray, gx2, MatType.CV_32F, 1, 0, 3);
        Cv2.Sobel(curGray, gy2, MatType.CV_32F, 0, 1, 3);

        Mat gxDiff = new Mat(); Mat gyDiff = new Mat();
        Cv2.Absdiff(gx1, gx2, gxDiff);
        Cv2.Absdiff(gy1, gy2, gyDiff);
        Mat gradCost = new Mat();
        Cv2.Add(gxDiff, gyDiff, gradCost);
        Mat cost = new Mat();
        Cv2.AddWeighted(colorDiff, 1.0, gradCost, 0.5, 0.0, cost); // CV_32F

        // 非重叠区给高代价，强制缝合线留在重叠区内
        Mat invMask = new Mat();
        Cv2.BitwiseNot(overlapMask, invMask); // 非重叠区=255，重叠区=0
        cost.SetTo(new Scalar(1e6), invMask);
        invMask.Dispose();

        // 3. 最佳缝合线 DP（每行 ±1 列，累计代价最小）
        int[] seam = BestSeamDp(cost, W, H);

        // 4. 融合：平滑权重 + 一致性选择
        Mat result = prev.Clone();
        // prevMask 外的区域，用 curGain 填充（直接 copy curGain where curMask>0 && prevMask==0）
        // 同时对重叠区用加权融合
        // 用逐像素处理：对每一行，seam_x 为缝合线位置
        //   x < seam_x - feather → 全 prev
        //   |x - seam_x| ≤ feather → smoothstep 权重
        //   x > seam_x + feather → 全 curGain

        var colorDiffArr = new byte[H * W];
        colorDiff.ConvertTo(new Mat(H, W, MatType.CV_8U), MatType.CV_8U); // 不能直接用，重新算
        // 简化：重新算一个 CV_8U 的颜色差供一致性选择
        Mat colorDiffU8 = new Mat();
        Cv2.Absdiff(prevGray, curGray, colorDiffU8);

        unsafe
        {
            byte* pResult = (byte*)result.Data;
            byte* pPrev = (byte*)prev.Data;
            byte* pCur = (byte*)curGain.Data;
            byte* pOverlap = (byte*)overlapMask.Data;
            byte* pColDiff = (byte*)colorDiffU8.Data;

            int diffThresh = 14; // 与 Python deghost_validate.py 一致
            for (int y = 0; y < H; y++)
            {
                int sx = seam[y];
                byte* rRow = pResult + y * W * 3;
                byte* pRow = pPrev + y * W * 3;
                byte* cRow = pCur + y * W * 3;
                byte* oRow = pOverlap + y * W;
                byte* dRow = pColDiff + y * W;
                for (int x = 0; x < W; x++)
                {
                    bool inOverlap = oRow[x] > 0;
                    bool curOnly = !inOverlap && curMask.At<byte>(y, x) > 0;
                    bool prevOnly = !inOverlap && prevMask.At<byte>(y, x) > 0;

                    if (prevOnly || (inOverlap && x < sx - feather))
                    {
                        // 全取 prev
                        rRow[x * 3] = pRow[x * 3];
                        rRow[x * 3 + 1] = pRow[x * 3 + 1];
                        rRow[x * 3 + 2] = pRow[x * 3 + 2];
                    }
                    else if (curOnly || (inOverlap && x > sx + feather))
                    {
                        // 全取 curGain
                        rRow[x * 3] = cRow[x * 3];
                        rRow[x * 3 + 1] = cRow[x * 3 + 1];
                        rRow[x * 3 + 2] = cRow[x * 3 + 2];
                    }
                    else if (inOverlap)
                    {
                        // 平滑过渡带 + 一致性选择
                        int dist = Math.Abs(x - sx);
                        double w = (feather <= 0) ? 0.0 :
                            0.5 * (1.0 - Math.Cos(Math.PI * Math.Min(dist, feather) / feather));
                        // smoothstep: w 从 0 (seam-feather) 到 1 (seam+feather)
                        // 距离 seam 越远（更靠近 cur 侧），权重越高
                        // 实际上 w = 0 在 seam-feather (prev 侧边界)，w = 1 在 seam+feather (cur 侧边界)
                        // 所以：result = (1-w)*prev + w*curGain
                        double wp = 1.0 - w; // prev 权重
                        double wc = w;       // curGain 权重

                        // 一致性选择：色差大时，取更靠近的一侧（不用平均）
                        if (dRow[x] > diffThresh)
                        {
                            if (dist <= feather / 2.0) { wp = 1.0; wc = 0.0; }
                            else { wp = 0.0; wc = 1.0; }
                        }

                        rRow[x * 3] = (byte)(wp * pRow[x * 3] + wc * cRow[x * 3]);
                        rRow[x * 3 + 1] = (byte)(wp * pRow[x * 3 + 1] + wc * cRow[x * 3 + 1]);
                        rRow[x * 3 + 2] = (byte)(wp * pRow[x * 3 + 2] + wc * cRow[x * 3 + 2]);
                    }
                    // 否则（两者都无效）保留原值
                }
            }
        }

        // 释放临时 Mat
        curGain.Dispose(); prevGray.Dispose(); curGray.Dispose();
        colorDiff.Dispose(); gx1.Dispose(); gy1.Dispose(); gx2.Dispose(); gy2.Dispose();
        gxDiff.Dispose(); gyDiff.Dispose(); gradCost.Dispose(); cost.Dispose();
        colorDiffU8.Dispose();
        return result;
    }
    catch (Exception ex)
    {
        PanoramaProtocol.Trace("FusePairDeghost 异常: " + ex.Message);
        return null;
    }
    finally { overlapMask.Dispose(); }
}

/// <summary>
/// 增益匹配：重叠区逐通道计算中值比 prev/cur，clamp 到 [0.6, 1.7]。
/// </summary>
private static double[] EstimateGain(Mat prev, Mat cur, Mat overlapMask)
{
    var gains = new double[3];
    Mat curCh = new Mat(); Mat prevCh = new Mat();
    try
    {
        for (int c = 0; c < 3; c++)
        {
            Cv2.ExtractChannel(prev, prevCh, c);
            Cv2.ExtractChannel(cur, curCh, c);

            // 取重叠区像素
            Mat prevRoi = new Mat(); Mat curRoi = new Mat();
            prevCh.CopyTo(prevRoi, overlapMask);
            curCh.CopyTo(curRoi, overlapMask);

            if (prevRoi.Empty() || curRoi.Empty() || Cv2.CountNonZero(overlapMask) < 50)
            {
                gains[c] = 1.0;
                prevRoi.Dispose(); curRoi.Dispose();
                continue;
            }

            // 用平均值代替中值（更简单，效果近似）
            Scalar pMean = Cv2.Mean(prevRoi);
            Scalar cMean = Cv2.Mean(curRoi);
            double pm = pMean.Val0;
            double cm = cMean.Val0;
            if (cm < 1e-3) gains[c] = 1.0;
            else gains[c] = pm / cm;
            gains[c] = Math.Max(0.6, Math.Min(1.7, gains[c]));

            prevRoi.Dispose(); curRoi.Dispose();
        }
    }
    finally { curCh.Dispose(); prevCh.Dispose(); }
    return gains;
}

/// <summary>
/// 最佳缝合线 DP：在 cost 图上找每行一条路径（允许 ±1 列跳变），累计代价最小。
/// </summary>
private static int[] BestSeamDp(Mat cost, int W, int H)
{
    int[] seam = new int[H];
    unsafe
    {
        float* c = (float*)cost.Data;
        // 动态规划：dp[y,x] = cost[y,x] + min(dp[y-1,x-1], dp[y-1,x], dp[y-1,x+1])
        // 用两个 1D 数组滚动
        float[] prevDp = new float[W];
        float[] currDp = new float[W];
        int[] parent = new int[W];

        for (int x = 0; x < W; x++) prevDp[x] = c[x];

        for (int y = 1; y < H; y++)
        {
            float* row = c + y * W;
            for (int x = 0; x < W; x++)
            {
                float best = float.MaxValue;
                int bestX = x;
                for (int dx = -1; dx <= 1; dx++)
                {
                    int nx = x + dx;
                    if (nx >= 0 && nx < W && prevDp[nx] < best)
                    {
                        best = prevDp[nx];
                        bestX = nx;
                    }
                }
                currDp[x] = row[x] + best;
                parent[x] = bestX;
            }
            // swap
            var tmp = prevDp; prevDp = currDp; currDp = tmp;
        }

        // 回朔：最后一行找最小值
        int seamX = 0;
        float minCost = float.MaxValue;
        for (int x = 0; x < W; x++)
        {
            if (prevDp[x] < minCost) { minCost = prevDp[x]; seamX = x; }
        }
        seam[H - 1] = seamX;
        for (int y = H - 2; y >= 0; y--)
        {
            seamX = parent[seamX];
            seam[y] = seamX;
        }
    }
    return seam;
}

/// <summary>
/// 兜底：简单羽化融合（Deghost 失败时调用，与 Blend 逻辑同但只对两幅图）。
/// </summary>
private static Mat FusePairFeather(Mat prev, Mat prevMask, Mat cur, Mat curMask, int ramp)
{
    try
    {
        Mat distP = new Mat(); Mat wP = new Mat(); Mat wP3 = new Mat();
        Mat distC = new Mat(); Mat wC = new Mat(); Mat wC3 = new Mat();

        Cv2.DistanceTransform(prevMask, distP, DistanceTypes.L2, DistanceTransformMasks.Mask3);
        distP.ConvertTo(wP, MatType.CV_32F, 1.0 / Math.Max(ramp, 1));
        Cv2.Threshold(wP, wP, 1.0, 1.0, ThresholdTypes.Trunc);

        Cv2.DistanceTransform(curMask, distC, DistanceTypes.L2, DistanceTransformMasks.Mask3);
        distC.ConvertTo(wC, MatType.CV_32F, 1.0 / Math.Max(ramp, 1));
        Cv2.Threshold(wC, wC, 1.0, 1.0, ThresholdTypes.Trunc);

        Mat sumW = new Mat();
        Cv2.Add(wP, wC, sumW);
        Cv2.Add(sumW, Scalar.All(1e-6), sumW);
        Cv2.Divide(wP, sumW, wP);
        Cv2.Divide(wC, sumW, wC);

        Cv2.Merge(new[] { wP, wP, wP }, wP3);
        Cv2.Merge(new[] { wC, wC, wC }, wC3);

        Mat prevF = new Mat(); Mat curF = new Mat();
        Mat prodP = new Mat(); Mat prodC = new Mat();
        prev.ConvertTo(prevF, MatType.CV_32FC3);
        cur.ConvertTo(curF, MatType.CV_32FC3);
        Cv2.Multiply(prevF, wP3, prodP);
        Cv2.Multiply(curF, wC3, prodC);
        Mat panoF = new Mat();
        Cv2.Add(prodP, prodC, panoF);
        Mat pano = new Mat();
        panoF.ConvertTo(pano, MatType.CV_8UC3);

        // 释放
        distP.Dispose(); wP.Dispose(); wP3.Dispose();
        distC.Dispose(); wC.Dispose(); wC3.Dispose();
        sumW.Dispose(); prevF.Dispose(); curF.Dispose();
        prodP.Dispose(); prodC.Dispose(); panoF.Dispose();

        return pano;
    }
    catch (Exception ex)
    {
        PanoramaProtocol.Trace("FusePairFeather 异常: " + ex.Message);
        return null;
    }
}
// ============================================================================

        /// <summary>裁掉四周全黑区域。</summary>
        private static Mat CropBlack(Mat input)
        {
            using (Mat gray = new Mat())
            using (Mat mask = new Mat())
            using (Mat idx = new Mat())
            {
                Cv2.CvtColor(input, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.Threshold(gray, mask, 6, 255, ThresholdTypes.Binary);
                Cv2.FindNonZero(mask, idx);
                if (idx.Empty())
                {
                    return input.Clone();
                }
                Rect rect = Cv2.BoundingRect(idx);
                if (rect.Width <= 0 || rect.Height <= 0)
                {
                    return input.Clone();
                }
                using (Mat roi = new Mat(input, rect))
                {
                    return roi.Clone();
                }
            }
        }

        private static void DetectSift(Mat bgr, out KeyPoint[] keypoints, out Mat descriptors)
        {
            keypoints = new KeyPoint[0];
            descriptors = null;
            using (Mat gray = ToGray(bgr))
            using (Mat equalized = new Mat())
            using (CLAHE clahe = Cv2.CreateCLAHE(2.0, new Size(8, 8)))
            using (SIFT sift = SIFT.Create(MaxFeatures, 3, 0.004, 10, 1.6))
            {
                clahe.Apply(gray, equalized);
                Mat desc = new Mat();
                sift.DetectAndCompute(equalized, null, out keypoints, desc, false);
                descriptors = desc;
            }
        }

        private static Mat ToGray(Mat src)
        {
            Mat gray = new Mat();
            if (src.Channels() == 3)
            {
                Cv2.CvtColor(src, gray, ColorConversionCodes.BGR2GRAY);
            }
            else if (src.Channels() == 4)
            {
                Cv2.CvtColor(src, gray, ColorConversionCodes.BGRA2GRAY);
            }
            else
            {
                gray = src.Clone();
            }
            return gray;
        }

        private static Mat EnsureBgr(Mat src)
        {
            if (src.Channels() == 3)
            {
                return src;
            }
            Mat bgr = new Mat();
            if (src.Channels() == 1)
            {
                Cv2.CvtColor(src, bgr, ColorConversionCodes.GRAY2BGR);
            }
            else if (src.Channels() == 4)
            {
                Cv2.CvtColor(src, bgr, ColorConversionCodes.BGRA2BGR);
            }
            else
            {
                return src;
            }
            return bgr;
        }

        /// <summary>点集转 CV_32FC2 的 Nx1 Mat，供 EstimateAffinePartial2D 使用。</summary>
        private static Mat PointsToMat(List<Point2f> points)
        {
            Mat m = new Mat(points.Count, 1, MatType.CV_32FC2);
            for (int i = 0; i < points.Count; i++)
            {
                m.Set<Vec2f>(i, 0, new Vec2f(points[i].X, points[i].Y));
            }
            return m;
        }

        /// <summary>3x3 双精度矩阵相乘。</summary>
        private static Mat Mul3(Mat a, Mat b)
        {
            Mat r = new Mat(3, 3, MatType.CV_64F);
            for (int i = 0; i < 3; i++)
            {
                for (int j = 0; j < 3; j++)
                {
                    double sum = 0;
                    for (int k = 0; k < 3; k++)
                    {
                        sum += a.At<double>(i, k) * b.At<double>(k, j);
                    }
                    r.Set<double>(i, j, sum);
                }
            }
            return r;
        }

        private static Mat Identity3()
        {
            Mat m = new Mat(3, 3, MatType.CV_64F, Scalar.All(0));
            m.Set<double>(0, 0, 1);
            m.Set<double>(1, 1, 1);
            m.Set<double>(2, 2, 1);
            return m;
        }

        private static Mat ToHomography(Mat affine2x3)
        {
            Mat h = Identity3();
            h.Set<double>(0, 0, affine2x3.At<double>(0, 0));
            h.Set<double>(0, 1, affine2x3.At<double>(0, 1));
            h.Set<double>(0, 2, affine2x3.At<double>(0, 2));
            h.Set<double>(1, 0, affine2x3.At<double>(1, 0));
            h.Set<double>(1, 1, affine2x3.At<double>(1, 1));
            h.Set<double>(1, 2, affine2x3.At<double>(1, 2));
            return h;
        }

        private class Candidate : IDisposable
        {
            public Mat Model;
            public double EccCc;
            public int Good;
            public int Inliers;
            public int KeypointsA;
            public int KeypointsB;

            public void Dispose()
            {
                Model?.Dispose();
                Model = null;
            }
        }
    }
}
