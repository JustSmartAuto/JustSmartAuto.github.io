using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Threading;
using JustSmart.Panorama;

namespace JustSmart.App
{
    /// <summary>
    /// 全景图拼接客户端：主程序检测完成、存图入队后，把「图片文件路径」任务放进内存队列，
    /// 由后台线程懒启动独立的 JustSmart.PanoramaWorker.exe 并通过命名管道发送 protobuf。
    /// 提交只入队列立即返回，不阻塞检测线程、ToolBlock 与 PLC 通信；任何失败静默降级。
    /// </summary>
    public static class PanoramaJobClient
    {
        private const string WorkerMutexName = "JustSmart.PanoramaWorker.SingleInstance";
        private const string WorkerExeName = "JustSmart.PanoramaWorker.exe";
        private const string WorkerFolderName = "PanoramaWorker";
        private const int ConnectTimeoutMs = 15000;
        private const int ConnectRetryIntervalMs = 250;

        private static readonly object SyncRoot = new object();
        private static readonly BlockingCollection<PanoramaJobMessage> Queue =
            new BlockingCollection<PanoramaJobMessage>();

        private static bool _started;
        private static long _nextJobId;

        /// <summary>拼接任务超时判定（工作进程异常/退出时避免任务永远显示进行中）</summary>
        private static readonly TimeSpan JobTimeout = TimeSpan.FromMinutes(10);

        /// <summary>任务跟踪表最多保留的条数</summary>
        private const int MaxTrackingEntries = 100;

        private static readonly object _trackingLock = new object();
        private static readonly List<PanoramaJobTracking> _tracking = new List<PanoramaJobTracking>();

        /// <summary>
        /// 提交一次拼接任务（幂等、非阻塞、不抛异常）。
        /// </summary>
        public static void Submit(PanoramaJobMessage job)
        {
            if (job == null)
            {
                return;
            }
            try
            {
                job.JobId = Interlocked.Increment(ref _nextJobId);
                job.TimestampMs = PanoramaProtocol.NowMs();
                EnsureStarted();
                if (Queue.TryAdd(job))
                {
                    TrackJob(job);
                }
                PanoramaProtocol.Trace(
                    "客户端: 已排队任务 "
                        + job.JobId
                        + "，分组 "
                        + (job.Groups?.Count ?? 0)
                        + "，目录 "
                        + job.SessionFolder);
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("客户端: 提交任务失败 " + ex.Message);
            }
        }

        /// <summary>
        /// 后台任务队列窗口使用的快照（按提交时间倒序）。每次调用都会检查输出文件，判断是否已完成。
        /// </summary>
        public static List<PanoramaJobSnapshot> GetSnapshot()
        {
            var result = new List<PanoramaJobSnapshot>();
            lock (_trackingLock)
            {
                for (int i = _tracking.Count - 1; i >= 0; i--)
                {
                    PanoramaJobTracking track = _tracking[i];
                    int completedGroups = 0;
                    foreach (string file in track.ExpectedFiles)
                    {
                        try
                        {
                            if (File.Exists(file))
                            {
                                completedGroups++;
                            }
                        }
                        catch
                        {
                            // 忽略单文件访问异常
                        }
                    }

                    bool allDone =
                        track.ExpectedFiles.Length > 0
                        && completedGroups >= track.ExpectedFiles.Length;
                    bool overTime = DateTime.Now - track.SubmittedAt > JobTimeout;

                    // 工作进程会在任务结束后写状态文件（OK / FAIL），据此结束"处理中"假象
                    string workerStatus = ReadJobStatus(track.JobId);
                    string stateText;
                    if (workerStatus == "OK" || allDone)
                    {
                        stateText = "已完成";
                    }
                    else if (workerStatus != null && workerStatus.StartsWith("FAIL"))
                    {
                        stateText = "失败";
                    }
                    else if (track.Sent && !IsWorkerRunning())
                    {
                        stateText = "失败";
                    }
                    else if (overTime)
                    {
                        stateText = track.Sent ? "超时" : "未启动";
                    }
                    else if (track.Sent)
                    {
                        stateText = completedGroups > 0 ? "拼接中" : "处理中";
                    }
                    else
                    {
                        stateText = "排队中";
                    }

                    result.Add(
                        new PanoramaJobSnapshot
                        {
                            JobId = track.JobId,
                            SubmittedAt = track.SubmittedAt,
                            ModeText = track.ModeText,
                            SessionFolder = track.SessionFolder,
                            GroupCount = track.GroupCount,
                            CompletedGroups = completedGroups,
                            ProgressText = completedGroups + "/" + track.GroupCount,
                            StateText = stateText,
                            ElapsedText =
                                (DateTime.Now - track.SubmittedAt).TotalSeconds.ToString("0.0")
                                + " s"
                        }
                    );
                }
            }
            return result;
        }

        /// <summary>拼接工作进程写入的任务状态目录（与工作进程约定一致）。</summary>
        private static string JobStatusDirectory =>
            Path.Combine(Path.GetTempPath(), "JustSmart_PanoramaStatus");

        private static string ReadJobStatus(long jobId)
        {
            try
            {
                string path = Path.Combine(JobStatusDirectory, jobId + ".txt");
                if (File.Exists(path))
                {
                    return File.ReadAllText(path).Trim();
                }
            }
            catch
            {
                // 读取状态失败按未知处理
            }
            return null;
        }

        private static bool IsWorkerRunning()
        {
            try
            {
                return Process.GetProcessesByName("JustSmart.PanoramaWorker").Length > 0;
            }
            catch
            {
                return true;
            }
        }

        private static void TrackJob(PanoramaJobMessage job)
        {
            var tracking = new PanoramaJobTracking
            {
                JobId = job.JobId,
                SessionFolder = job.SessionFolder ?? "",
                SubmittedAt = DateTime.Now,
                GroupCount = job.Groups?.Count ?? 0,
                ModeText = ResolveModeText(job.SessionFolder),
                ExpectedFiles = BuildExpectedFiles(job)
            };
            lock (_trackingLock)
            {
                _tracking.Add(tracking);
                while (_tracking.Count > MaxTrackingEntries)
                {
                    _tracking.RemoveAt(0);
                }
            }
        }

        private static void MarkSent(long jobId)
        {
            lock (_trackingLock)
            {
                for (int i = 0; i < _tracking.Count; i++)
                {
                    if (_tracking[i].JobId == jobId)
                    {
                        _tracking[i].Sent = true;
                        return;
                    }
                }
            }
        }

        /// <summary>根据存图目录判断模式：AutoInspection=自动，ManualInspection=手动。</summary>
        private static string ResolveModeText(string sessionFolder)
        {
            if (string.IsNullOrWhiteSpace(sessionFolder))
            {
                return "未知";
            }
            if (sessionFolder.IndexOf("AutoInspection", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "自动";
            }
            if (sessionFolder.IndexOf("ManualInspection", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "手动";
            }
            return "其他";
        }

        /// <summary>
        /// 工作进程写出的文件路径（与 PanoramaServer.SavePanorama 命名保持一致），用于判断任务是否完成。
        /// </summary>
        private static string[] BuildExpectedFiles(PanoramaJobMessage job)
        {
            var files = new List<string>();
            if (job == null || job.Groups == null || string.IsNullOrWhiteSpace(job.OutputFolder))
            {
                return files.ToArray();
            }

            string format = (job.ImageFormat ?? "").Trim().ToUpperInvariant();
            string ext = format == "BMP" ? ".bmp" : format == "PNG" ? ".png" : ".jpg";
            string stamp = string.IsNullOrWhiteSpace(job.Stamp)
                ? DateTime.Now.ToString("yyyyMMdd_HHmmss")
                : job.Stamp.Trim();
            foreach (PanoramaGroupMessage group in job.Groups)
            {
                files.Add(
                    Path.Combine(
                        job.OutputFolder,
                        "LineAVI_" + stamp + "_" + group.GroupName + "_Stitched" + ext
                    )
                );
            }
            return files.ToArray();
        }

        private sealed class PanoramaJobTracking
        {
            public long JobId;
            public string SessionFolder;
            public string ModeText;
            public int GroupCount;
            public string[] ExpectedFiles;
            public DateTime SubmittedAt;
            public bool Sent;
        }

        /// <summary>
        /// 等待九张原图全部落盘（大小大于 0 且稳定）后再提交拼接任务。
        /// 避免工作进程在存图队列尚未写完时读取，导致读文件失败、图片变 0 字节。
        /// 后台线程执行，不阻塞检测流程。
        /// </summary>
        public static void SubmitWhenFilesReady(PanoramaJobMessage job)
        {
            if (job == null)
            {
                return;
            }
            var thread = new Thread(() =>
            {
                try
                {
                    var paths = new List<string>();
                    if (job.Groups != null)
                    {
                        foreach (PanoramaGroupMessage group in job.Groups)
                        {
                            if (group?.Inputs == null)
                            {
                                continue;
                            }
                            foreach (PanoramaInputMessage input in group.Inputs)
                            {
                                if (input != null && !string.IsNullOrWhiteSpace(input.FilePath))
                                {
                                    paths.Add(input.FilePath);
                                }
                            }
                        }
                    }

                    if (WaitForFilesReady(paths, TimeSpan.FromSeconds(120)))
                    {
                        Submit(job);
                    }
                    else
                    {
                        PanoramaProtocol.Trace(
                            "客户端: 等待原图落盘超时（120s），跳过本次拼接，目录 "
                                + job.SessionFolder);
                    }
                }
                catch (Exception ex)
                {
                    PanoramaProtocol.Trace("客户端: 等待原图落盘异常 " + ex.Message);
                }
            })
            {
                IsBackground = true,
                Name = "PanoramaSubmitWaiter"
            };
            thread.Start();
        }

        /// <summary>所有文件都存在、大小大于 0 且连续 800ms 大小不变，才认为写入完成。</summary>
        private static bool WaitForFilesReady(List<string> paths, TimeSpan timeout)
        {
            if (paths.Count == 0)
            {
                return false;
            }

            var sizes = new Dictionary<string, long>();
            var stableSince = new Dictionary<string, DateTime>();
            foreach (string path in paths)
            {
                sizes[path] = -1;
                stableSince[path] = DateTime.MinValue;
            }

            Stopwatch watch = Stopwatch.StartNew();
            while (watch.Elapsed < timeout)
            {
                bool allReady = true;
                foreach (string path in paths)
                {
                    long length = -1;
                    try
                    {
                        var info = new FileInfo(path);
                        if (info.Exists)
                        {
                            length = info.Length;
                        }
                    }
                    catch
                    {
                        length = -1;
                    }

                    if (length <= 0)
                    {
                        sizes[path] = -1;
                        allReady = false;
                        continue;
                    }

                    if (sizes[path] != length)
                    {
                        sizes[path] = length;
                        stableSince[path] = DateTime.Now;
                        allReady = false;
                    }
                    else if ((DateTime.Now - stableSince[path]).TotalMilliseconds < 800)
                    {
                        allReady = false;
                    }
                }

                if (allReady)
                {
                    return true;
                }
                Thread.Sleep(200);
            }
            return false;
        }

        private static void EnsureStarted()
        {
            lock (SyncRoot)
            {
                if (_started)
                {
                    return;
                }
                _started = true;
            }

            // 每次进程启动清空上次运行的任务状态文件（任务号每次从 1 开始，避免串号）
            try
            {
                string statusDir = JobStatusDirectory;
                if (Directory.Exists(statusDir))
                {
                    Directory.Delete(statusDir, true);
                }
            }
            catch
            {
                // 清理失败不影响拼接
            }
            var thread = new Thread(SenderLoop)
            {
                IsBackground = true,
                Name = "PanoramaJobPipeClient"
            };
            thread.Start();
        }

        private static void SenderLoop()
        {
            NamedPipeClientStream client = null;
            try
            {
                client = ConnectOrStart();
                if (client == null)
                {
                    PanoramaProtocol.Trace("客户端: 未找到拼接工作进程，本次拼接跳过");
                    return;
                }

                foreach (PanoramaJobMessage job in Queue.GetConsumingEnumerable())
                {
                    try
                    {
                        SendJob(client, job);
                    }
                    catch (Exception ex)
                    {
                        PanoramaProtocol.Trace("客户端: 发送失败，尝试重连 " + ex.Message);
                        try
                        {
                            client.Dispose();
                        }
                        catch
                        {
                            // 忽略
                        }
                        client = ConnectOrStart();
                        if (client == null)
                        {
                            PanoramaProtocol.Trace("客户端: 重连失败，放弃后续拼接任务");
                            return;
                        }
                        SendJob(client, job);
                    }
                }
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("客户端通信结束: " + ex);
            }
            finally
            {
                if (client != null)
                {
                    try
                    {
                        client.Dispose();
                    }
                    catch
                    {
                        // 忽略
                    }
                }
            }
        }

        private static void SendJob(NamedPipeClientStream client, PanoramaJobMessage job)
        {
            byte[] payload = PanoramaProtocol.SerializeJob(job);
            PanoramaProtocol.WriteFrame(client, payload);
            MarkSent(job.JobId);
            PanoramaProtocol.Trace("客户端: 已发送任务 " + job.JobId);
        }

        /// <summary>
        /// 连接工作进程；连不上时先尝试拉起工作进程，再在超时时间内重试。
        /// </summary>
        private static NamedPipeClientStream ConnectOrStart()
        {
            long start = PanoramaProtocol.NowMs();
            bool launchTried = false;
            while (PanoramaProtocol.NowMs() - start < ConnectTimeoutMs)
            {
                NamedPipeClientStream client = null;
                try
                {
                    client = new NamedPipeClientStream(
                        ".",
                        PanoramaProtocol.PipeName,
                        PipeDirection.Out,
                        PipeOptions.None);
                    client.Connect(ConnectRetryIntervalMs);
                    PanoramaProtocol.Trace("客户端: 已连接拼接工作进程");
                    return client;
                }
                catch (Exception ex) when (ex is TimeoutException || ex is IOException)
                {
                    if (client != null)
                    {
                        client.Dispose();
                    }
                    if (!launchTried)
                    {
                        launchTried = true;
                        LaunchWorker();
                    }
                    Thread.Sleep(ConnectRetryIntervalMs);
                }
            }
            return null;
        }

        private static void LaunchWorker()
        {
            try
            {
                if (Mutex.TryOpenExisting(WorkerMutexName, out _))
                {
                    return; // 工作进程已在运行，只是尚未就绪
                }
            }
            catch
            {
                // 权限问题等，继续尝试启动
            }

            try
            {
                string workerDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, WorkerFolderName);
                string exePath = Path.Combine(workerDir, WorkerExeName);
                if (!File.Exists(exePath))
                {
                    PanoramaProtocol.Trace("客户端: 未找到工作进程 " + exePath);
                    return;
                }
                var startInfo = new ProcessStartInfo(exePath)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = workerDir
                };
                Process.Start(startInfo);
                PanoramaProtocol.Trace("客户端: 已启动拼接工作进程");
            }
            catch (Exception ex)
            {
                PanoramaProtocol.Trace("客户端: 启动工作进程失败 " + ex.Message);
            }
        }
    }

    /// <summary>
    /// 单个拼接任务的展示快照（后台任务队列窗口使用）。
    /// </summary>
    public sealed class PanoramaJobSnapshot
    {
        public long JobId { get; set; }
        public DateTime SubmittedAt { get; set; }
        public string ModeText { get; set; }
        public string SessionFolder { get; set; }
        public int GroupCount { get; set; }
        public int CompletedGroups { get; set; }
        public string ProgressText { get; set; }
        public string StateText { get; set; }
        public string ElapsedText { get; set; }
    }
}
