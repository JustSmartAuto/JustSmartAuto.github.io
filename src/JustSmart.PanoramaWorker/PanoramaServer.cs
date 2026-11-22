using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Threading;
using OpenCvSharp;

namespace JustSmart.Panorama
{
    /// <summary>
    /// 拼接工作进程服务端：命名管道接收主程序投递的任务（protobuf），
    /// 交给独立的工作线程顺序执行，失败只记录日志。
    /// </summary>
    internal sealed class PanoramaServer
    {
        /// <summary>无连接时的最长存活时间，避免主程序退出后工作进程永久残留</summary>
        private static readonly TimeSpan IdleTimeout = TimeSpan.FromMinutes(10);

        /// <summary>拼接处理尺度（0.5 兼顾速度与精度，与现场验证结果一致）</summary>
        private const double ProcessScale = 0.5;

        /// <summary>羽化宽度（处理尺度下像素）</summary>
        private const int Ramp = 80;

        /// <summary>羽化权重指数</summary>
        private const int Sharpness = 4;

        /// <summary>等待图片落盘的最长时间</summary>
        private const int FileWaitTimeoutMs = 60000;

        private const int FilePollIntervalMs = 200;

        private readonly BlockingCollection<PanoramaJobMessage> _jobs =
            new BlockingCollection<PanoramaJobMessage>();

        private Thread _workerThread;

        /// <summary>启动处理线程并阻塞运行管道服务循环，直到空闲超时。</summary>
        public void Run()
        {
            _workerThread = new Thread(ProcessLoop)
            {
                IsBackground = true,
                Name = "PanoramaJobProcessor",
                Priority = ThreadPriority.BelowNormal
            };
            _workerThread.Start();

            try
            {
                ServerLoop();
            }
            finally
            {
                try
                {
                    _jobs.CompleteAdding();
                }
                catch
                {
                    // 忽略
                }
                try
                {
                    _workerThread.Join();
                }
                catch
                {
                    // 忽略
                }
            }
        }

        private void ServerLoop()
        {
            while (true)
            {
                NamedPipeServerStream server = null;
                try
                {
                    server = new NamedPipeServerStream(
                        PanoramaProtocol.PipeName,
                        PipeDirection.In,
                        1,
                        PipeTransmissionMode.Byte,
                        PipeOptions.Asynchronous);

                    PanoramaProtocol.Trace("服务端: 等待主程序连接");
                    IAsyncResult wait = server.BeginWaitForConnection(null, null);
                    if (!wait.AsyncWaitHandle.WaitOne((int)IdleTimeout.TotalMilliseconds))
                    {
                        PanoramaProtocol.Trace("服务端: 空闲超时，工作进程退出");
                        return;
                    }
                    server.EndWaitForConnection(wait);
                    PanoramaProtocol.Trace("服务端: 已连接主程序");

                    byte[] payload;
                    while (PanoramaProtocol.ReadFrame(server, out payload))
                    {
                        try
                        {
                            PanoramaJobMessage job = PanoramaProtocol.DeserializeJob(payload);
                            _jobs.Add(job);
                            PanoramaProtocol.Trace(
                                "服务端: 收到任务 " + job.JobId + "，分组 " + job.Groups.Count);
                        }
                        catch (Exception ex)
                        {
                            PanoramaProtocol.Trace("服务端: 任务解析失败 " + ex.Message);
                        }
                    }
                    PanoramaProtocol.Trace("服务端: 主程序断开连接");
                }
                catch (Exception ex)
                {
                    PanoramaProtocol.Trace("服务端: 通信异常 " + ex.Message);
                }
                finally
                {
                    if (server != null)
                    {
                        try
                        {
                            server.Dispose();
                        }
                        catch
                        {
                            // 忽略
                        }
                    }
                }
            }
        }

        private void ProcessLoop()
        {
            foreach (PanoramaJobMessage job in _jobs.GetConsumingEnumerable())
            {
                Stopwatch watch = Stopwatch.StartNew();
                bool success = false;
                try
                {
                    success = ProcessJob(job);
                }
                catch (Exception ex)
                {
                    PanoramaProtocol.Trace("任务 " + job.JobId + " 处理异常: " + ex);
                }
                finally
                {
                    PanoramaProtocol.Trace(
                        "任务 " + job.JobId + " 结束，耗时 " + watch.ElapsedMilliseconds + " ms");
                    WriteJobStatus(job.JobId, success ? "OK" : "FAIL");
                }
            }
        }

        private static bool ProcessJob(PanoramaJobMessage job)
        {
            if (job.Groups == null || job.Groups.Count == 0)
            {
                PanoramaProtocol.Trace("任务 " + job.JobId + " 无分组，跳过");
                return false;
            }
            string outputFolder = job.OutputFolder;
            if (string.IsNullOrWhiteSpace(outputFolder) && !string.IsNullOrWhiteSpace(job.SessionFolder))
            {
                outputFolder = Path.Combine(job.SessionFolder, "StitchedImage");
            }
            if (string.IsNullOrWhiteSpace(outputFolder))
            {
                PanoramaProtocol.Trace("任务 " + job.JobId + " 无输出目录，跳过");
                return false;
            }
            try
            {
                Directory.CreateDirectory(outputFolder);
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("任务 " + job.JobId + " 创建输出目录失败: " + ex.Message);
                return false;
            }

            bool allGroupsOk = true;
            foreach (PanoramaGroupMessage group in job.Groups)
            {
                try
                {
                    if (!ProcessGroup(job, group, outputFolder))
                    {
                        allGroupsOk = false;
                    }
                }
                catch (Exception ex)
                {
                    allGroupsOk = false;
                    PanoramaProtocol.Trace(
                        "任务 " + job.JobId + " 分组 " + group.GroupName + " 异常: " + ex);
                }
            }
            return allGroupsOk;
        }

        /// <summary>写入任务状态文件，供主程序后台任务队列判断成功/失败。</summary>
        private static void WriteJobStatus(long jobId, string status)
        {
            try
            {
                string dir = Path.Combine(Path.GetTempPath(), "JustSmart_PanoramaStatus");
                Directory.CreateDirectory(dir);
                File.WriteAllText(Path.Combine(dir, jobId + ".txt"), status);
            }
            catch
            {
                // 状态写失败不影响拼接
            }
        }

        private static bool ProcessGroup(
            PanoramaJobMessage job,
            PanoramaGroupMessage group,
            string outputFolder
        )
        {
            if (group == null || group.Inputs == null || group.Inputs.Count < 2)
            {
                PanoramaProtocol.Trace("分组 " + (group?.GroupName ?? "?") + " 输入不足，跳过");
                return false;
            }

            var images = new List<Mat>();
            var names = new List<string>();
            try
            {
                foreach (PanoramaInputMessage input in group.Inputs)
                {
                    string path = ResolveReadablePath(input.FilePath);
                    if (path == null)
                    {
                        PanoramaProtocol.Trace(
                            "分组 " + group.GroupName + " 输入不可读（CDB 或其他格式）: " + input.FilePath);
                        return false;
                    }
                    if (!WaitForFile(path))
                    {
                        PanoramaProtocol.Trace("分组 " + group.GroupName + " 等待图片超时: " + path);
                        return false;
                    }
                    Mat image = LoadDownsampled(path);
                    if (image == null)
                    {
                        PanoramaProtocol.Trace("分组 " + group.GroupName + " 解码失败: " + path);
                        return false;
                    }
                    images.Add(image);
                    names.Add(input.CameraName ?? "");
                }

                Mat panorama;
                List<PanoramaPairInfo> pairs;
                // SideBCam1 在 SideBCam2 斜上方、视野更近，配准兜底需要同时搜索尺度
                bool allowScaleFallback = "SideBCam".Equals(
                    group.GroupName,
                    StringComparison.OrdinalIgnoreCase
                );

                // 算法选择：Kornia 占位未实现，降级为 Baseline 并记日志
                PanoramaAlgorithmId algo = job.Algorithm;
                if (algo == PanoramaAlgorithmId.Kornia)
                {
                    PanoramaProtocol.Trace(
                        "任务 " + job.JobId + " 分组 " + group.GroupName
                        + " 请求 Kornia 算法（占位），降级为 Baseline");
                    algo = PanoramaAlgorithmId.Baseline;
                }

                if (!PanoramaStitcher.TryStitch(images, names, out panorama, out pairs,
                    1.0, Ramp, Sharpness, allowScaleFallback, algo))
                {
                    PanoramaProtocol.Trace("分组 " + group.GroupName + " 拼接失败");
                    return false;
                }
                try
                {
                    SavePanorama(job, group, panorama, outputFolder, pairs);
                }
                finally
                {
                    panorama.Dispose();
                }
                return true;
            }
            finally
            {
                foreach (Mat m in images)
                {
                    m.Dispose();
                }
            }
        }

        /// <summary>
        /// CDB 无法用 OpenCV 解码，尝试同名 png/bmp/jpg；返回 null 表示该图不可读。
        /// </summary>
        private static string ResolveReadablePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return null;
            }
            if (!".cdb".Equals(Path.GetExtension(path), StringComparison.OrdinalIgnoreCase))
            {
                return path;
            }
            string basePath = Path.ChangeExtension(path, null);
            string[] candidates = { ".png", ".bmp", ".jpg", ".jpeg" };
            foreach (string ext in candidates)
            {
                string candidate = basePath + ext;
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
            return null;
        }

        /// <summary>等待图片落盘：文件存在、非空且大小稳定。</summary>
        private static bool WaitForFile(string path)
        {
            Stopwatch watch = Stopwatch.StartNew();
            long lastSize = -1;
            while (watch.ElapsedMilliseconds < FileWaitTimeoutMs)
            {
                try
                {
                    var info = new FileInfo(path);
                    if (info.Exists && info.Length > 0)
                    {
                        if (info.Length == lastSize && CanOpenForRead(path))
                        {
                            return true;
                        }
                        lastSize = info.Length;
                    }
                }
                catch
                {
                    // 文件可能正被写入，继续轮询
                }
                Thread.Sleep(FilePollIntervalMs);
            }
            return false;
        }

        /// <summary>确认文件可被读取（未被存图线程独占），避免读到正在写入的 0 字节文件。</summary>
        private static bool CanOpenForRead(string path)
        {
            try
            {
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    return stream.Length > 0;
                }
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// 从磁盘读取并降采样。用 ImDecode 而非 ImRead，规避 OpenCV 对中文路径的读取问题。
        /// </summary>
        private static Mat LoadDownsampled(string path)
        {
            Mat raw = null;
            try
            {
                byte[] bytes = File.ReadAllBytes(path);
                raw = Cv2.ImDecode(bytes, ImreadModes.Color);
                if (raw == null || raw.Empty())
                {
                    return null;
                }
                Mat small = new Mat();
                Cv2.Resize(raw, small, new Size(), ProcessScale, ProcessScale, InterpolationFlags.Area);
                return small;
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("读取图片失败: " + path + " " + ex.Message);
                return null;
            }
            finally
            {
                raw?.Dispose();
            }
        }

        private static void SavePanorama(
            PanoramaJobMessage job,
            PanoramaGroupMessage group,
            Mat panorama,
            string outputFolder,
            List<PanoramaPairInfo> pairs
        )
        {
            string format = (job.ImageFormat ?? "").Trim().ToUpperInvariant();
            string ext;
            var encoding = new List<ImageEncodingParam>();
            switch (format)
            {
                case "BMP":
                    ext = ".bmp";
                    break;
                case "PNG":
                    ext = ".png";
                    break;
                default:
                    // JPG 与 CDB 无法由 Mat 写出原格式，统一按 JPG 保存
                    ext = ".jpg";
                    int quality = Math.Max(1, Math.Min(100, job.JpegQuality));
                    encoding.Add(new ImageEncodingParam(ImwriteFlags.JpegQuality, quality));
                    break;
            }

            string stamp = string.IsNullOrWhiteSpace(job.Stamp)
                ? DateTime.Now.ToString("yyyyMMdd_HHmmss")
                : job.Stamp.Trim();
            string path = Path.Combine(
                outputFolder,
                "LineAVI_" + stamp + "_" + group.GroupName + "_Stitched" + ext
            );
            try
            {
                // ImEncode + WriteAllBytes 规避 OpenCV 对中文路径的写入问题
                byte[] buffer;
                Cv2.ImEncode(ext, panorama, out buffer, encoding.ToArray());
                File.WriteAllBytes(path, buffer);
                PanoramaProtocol.Trace(
                    "分组 "
                        + group.GroupName
                        + " 已保存: "
                        + path
                        + " ("
                        + panorama.Width
                        + "x"
                        + panorama.Height
                        + ", 配准对 "
                        + (pairs?.Count ?? 0)
                        + ")"
                );
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("分组 " + group.GroupName + " 保存失败: " + ex.Message);
            }
        }
    }
}
