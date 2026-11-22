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
            bool allowScaleFallback = false
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

                panorama = Blend(scaled, transforms, ramp, sharpness);
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
