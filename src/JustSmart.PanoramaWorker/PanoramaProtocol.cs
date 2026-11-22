using System;
using System.Collections.Generic;
using System.IO;
using Google.Protobuf;

namespace JustSmart.Panorama
{
    /// <summary>
    /// 全景图拼接 IPC 协议（protobuf 3 线格式），与 panorama_job.proto 对应：
    ///   message PanoramaJobMessage { ... repeated PanoramaGroupMessage groups = 7; }
    ///   message PanoramaGroupMessage { string group_name = 1; repeated PanoramaInputMessage inputs = 2; }
    ///   message PanoramaInputMessage { string camera_name = 1; string file_path = 2; }
    /// 帧格式：4 字节小端长度 + protobuf 载荷，与 StartupBannerProtocol 一致。
    /// </summary>
    public static class PanoramaProtocol
    {
        /// <summary>命名管道名称（本地）</summary>
        public const string PipeName = "JustSmart.SeatBeltInspection.Panorama";

        /// <summary>单帧最大长度：任务只含文件路径，1MB 足够并防止异常长度</summary>
        public const int MaxFrameLength = 1024 * 1024;

        // PanoramaJobMessage
        public const int TagJobId = 1;
        public const int TagSessionFolder = 2;
        public const int TagOutputFolder = 3;
        public const int TagImageFormat = 4;
        public const int TagJpegQuality = 5;
        public const int TagStamp = 6;
        public const int TagGroups = 7;
        public const int TagTimestamp = 8;
        public const int TagAlgorithm = 9;

        // PanoramaGroupMessage
        public const int TagGroupName = 1;
        public const int TagGroupInputs = 2;

        // PanoramaInputMessage
        public const int TagCameraName = 1;
        public const int TagFilePath = 2;

        public static byte[] SerializeJob(PanoramaJobMessage job)
        {
            using (MemoryStream stream = new MemoryStream())
            {
                CodedOutputStream output = new CodedOutputStream(stream);
                if (job.JobId != 0)
                {
                    output.WriteTag(TagJobId, WireFormat.WireType.Varint);
                    output.WriteInt64(job.JobId);
                }
                if (!string.IsNullOrEmpty(job.SessionFolder))
                {
                    output.WriteTag(TagSessionFolder, WireFormat.WireType.LengthDelimited);
                    output.WriteString(job.SessionFolder);
                }
                if (!string.IsNullOrEmpty(job.OutputFolder))
                {
                    output.WriteTag(TagOutputFolder, WireFormat.WireType.LengthDelimited);
                    output.WriteString(job.OutputFolder);
                }
                if (!string.IsNullOrEmpty(job.ImageFormat))
                {
                    output.WriteTag(TagImageFormat, WireFormat.WireType.LengthDelimited);
                    output.WriteString(job.ImageFormat);
                }
                if (job.JpegQuality != 0)
                {
                    output.WriteTag(TagJpegQuality, WireFormat.WireType.Varint);
                    output.WriteInt32(job.JpegQuality);
                }
                if (!string.IsNullOrEmpty(job.Stamp))
                {
                    output.WriteTag(TagStamp, WireFormat.WireType.LengthDelimited);
                    output.WriteString(job.Stamp);
                }
                if (job.Groups != null)
                {
                    foreach (PanoramaGroupMessage group in job.Groups)
                    {
                        output.WriteTag(TagGroups, WireFormat.WireType.LengthDelimited);
                        output.WriteBytes(ByteString.CopyFrom(SerializeGroup(group)));
                    }
                }
                if (job.TimestampMs != 0)
                {
                    output.WriteTag(TagTimestamp, WireFormat.WireType.Varint);
                    output.WriteInt64(job.TimestampMs);
                }
                if ((int)job.Algorithm != 0)
                {
                    output.WriteTag(TagAlgorithm, WireFormat.WireType.Varint);
                    output.WriteInt32((int)job.Algorithm);
                }
                output.Flush();
                return stream.ToArray();
            }
        }

        public static PanoramaJobMessage DeserializeJob(byte[] data)
        {
            var job = new PanoramaJobMessage();
            CodedInputStream input = new CodedInputStream(data);
            uint tag;
            while ((tag = input.ReadTag()) != 0)
            {
                switch (WireFormat.GetTagFieldNumber(tag))
                {
                    case TagJobId:
                        job.JobId = input.ReadInt64();
                        break;
                    case TagSessionFolder:
                        job.SessionFolder = input.ReadString();
                        break;
                    case TagOutputFolder:
                        job.OutputFolder = input.ReadString();
                        break;
                    case TagImageFormat:
                        job.ImageFormat = input.ReadString();
                        break;
                    case TagJpegQuality:
                        job.JpegQuality = input.ReadInt32();
                        break;
                    case TagStamp:
                        job.Stamp = input.ReadString();
                        break;
                    case TagGroups:
                        job.Groups.Add(DeserializeGroup(input.ReadBytes().ToByteArray()));
                        break;
                    case TagTimestamp:
                        job.TimestampMs = input.ReadInt64();
                        break;
                    case TagAlgorithm:
                        job.Algorithm = (PanoramaAlgorithmId)input.ReadInt32();
                        break;
                    default:
                        input.SkipLastField();
                        break;
                }
            }
            return job;
        }

        private static byte[] SerializeGroup(PanoramaGroupMessage group)
        {
            using (MemoryStream stream = new MemoryStream())
            {
                CodedOutputStream output = new CodedOutputStream(stream);
                if (!string.IsNullOrEmpty(group.GroupName))
                {
                    output.WriteTag(TagGroupName, WireFormat.WireType.LengthDelimited);
                    output.WriteString(group.GroupName);
                }
                if (group.Inputs != null)
                {
                    foreach (PanoramaInputMessage item in group.Inputs)
                    {
                        output.WriteTag(TagGroupInputs, WireFormat.WireType.LengthDelimited);
                        output.WriteBytes(ByteString.CopyFrom(SerializeInput(item)));
                    }
                }
                output.Flush();
                return stream.ToArray();
            }
        }

        private static PanoramaGroupMessage DeserializeGroup(byte[] data)
        {
            var group = new PanoramaGroupMessage();
            CodedInputStream input = new CodedInputStream(data);
            uint tag;
            while ((tag = input.ReadTag()) != 0)
            {
                switch (WireFormat.GetTagFieldNumber(tag))
                {
                    case TagGroupName:
                        group.GroupName = input.ReadString();
                        break;
                    case TagGroupInputs:
                        group.Inputs.Add(DeserializeInput(input.ReadBytes().ToByteArray()));
                        break;
                    default:
                        input.SkipLastField();
                        break;
                }
            }
            return group;
        }

        private static byte[] SerializeInput(PanoramaInputMessage item)
        {
            using (MemoryStream stream = new MemoryStream())
            {
                CodedOutputStream output = new CodedOutputStream(stream);
                if (!string.IsNullOrEmpty(item.CameraName))
                {
                    output.WriteTag(TagCameraName, WireFormat.WireType.LengthDelimited);
                    output.WriteString(item.CameraName);
                }
                if (!string.IsNullOrEmpty(item.FilePath))
                {
                    output.WriteTag(TagFilePath, WireFormat.WireType.LengthDelimited);
                    output.WriteString(item.FilePath);
                }
                output.Flush();
                return stream.ToArray();
            }
        }

        private static PanoramaInputMessage DeserializeInput(byte[] data)
        {
            var item = new PanoramaInputMessage();
            CodedInputStream input = new CodedInputStream(data);
            uint tag;
            while ((tag = input.ReadTag()) != 0)
            {
                switch (WireFormat.GetTagFieldNumber(tag))
                {
                    case TagCameraName:
                        item.CameraName = input.ReadString();
                        break;
                    case TagFilePath:
                        item.FilePath = input.ReadString();
                        break;
                    default:
                        input.SkipLastField();
                        break;
                }
            }
            return item;
        }

        public static long NowMs()
        {
            return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
        }

        /// <summary>写诊断日志（工作进程无界面，异常只能落盘排查）。</summary>
        public static void Trace(string message)
        {
            try
            {
                string path = Path.Combine(Path.GetTempPath(), "JustSmart_PanoramaWorker.log");
                File.AppendAllText(
                    path,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + Environment.NewLine);
            }
            catch
            {
                // 日志失败不影响主流程
            }
        }

        public static void WriteFrame(Stream stream, byte[] payload)
        {
            byte[] header = BitConverter.GetBytes(payload.Length);
            if (!BitConverter.IsLittleEndian)
                Array.Reverse(header);
            stream.Write(header, 0, header.Length);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }

        public static bool ReadFrame(Stream stream, out byte[] payload)
        {
            payload = null;
            byte[] header = new byte[4];
            if (!ReadExact(stream, header, header.Length))
                return false;
            if (!BitConverter.IsLittleEndian)
                Array.Reverse(header);
            int length = BitConverter.ToInt32(header, 0);
            if (length <= 0 || length > MaxFrameLength)
                return false;
            byte[] body = new byte[length];
            if (!ReadExact(stream, body, length))
                return false;
            payload = body;
            return true;
        }

        private static bool ReadExact(Stream stream, byte[] buffer, int count)
        {
            int offset = 0;
            while (offset < count)
            {
                int read = stream.Read(buffer, offset, count - offset);
                if (read <= 0)
                    return false;
                offset += read;
            }
            return true;
        }
    }

    /// <summary>一次拼接任务：会话目录、输出目录、若干相机分组。</summary>
    public sealed class PanoramaJobMessage
    {
        public PanoramaJobMessage()
        {
            Groups = new List<PanoramaGroupMessage>();
            ImageFormat = "PNG";
            JpegQuality = 92;
            Algorithm = PanoramaAlgorithmId.Baseline;
        }

        public long JobId { get; set; }
        public string SessionFolder { get; set; }
        public string OutputFolder { get; set; }
        public string ImageFormat { get; set; }
        public int JpegQuality { get; set; }
        public string Stamp { get; set; }
        public List<PanoramaGroupMessage> Groups { get; private set; }
        public long TimestampMs { get; set; }
        public PanoramaAlgorithmId Algorithm { get; set; }
    }

    /// <summary>一组按物理从左到右排列的相机图像（TopCam / SideBCam）。</summary>
    public sealed class PanoramaGroupMessage
    {
        public PanoramaGroupMessage()
        {
            Inputs = new List<PanoramaInputMessage>();
        }

        public string GroupName { get; set; }
        public List<PanoramaInputMessage> Inputs { get; private set; }
    }

    /// <summary>单相机输入：相机名 + 已入队保存的文件路径。</summary>
    public sealed class PanoramaInputMessage
    {
        public string CameraName { get; set; }
        public string FilePath { get; set; }
    }
    /// <summary>
    /// 拼接算法标识（IPC 线格式用；值与主程序 JustSmart.App.CameraWork.PanoramaAlgorithm 对齐）。
    /// </summary>
    public enum PanoramaAlgorithmId
    {
        Baseline = 0,
        Deghost = 1,
        Kornia = 2
    }
}