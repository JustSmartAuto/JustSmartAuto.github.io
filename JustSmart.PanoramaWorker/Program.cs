using System;
using System.Diagnostics;
using System.Threading;
using JustSmart.Panorama;

namespace JustSmart.PanoramaWorker
{
    /// <summary>
    /// 全景图拼接独立工作进程入口：主程序检测完成、存图入队后按需拉起，
    /// 通过命名管道接收 protobuf 任务，专职做 SIFT 拼接，不参与检测与 PLC 链路。
    /// </summary>
    internal static class Program
    {
        private const string SingleInstanceMutexName = "JustSmart.PanoramaWorker.SingleInstance";

        [STAThread]
        private static void Main()
        {
            bool createdNew;
            using (var mutex = new Mutex(true, SingleInstanceMutexName, out createdNew))
            {
                if (!createdNew)
                {
                    return;
                }

                try
                {
                    // 低优先级运行，避免与检测线程争抢 CPU
                    Process.GetCurrentProcess().PriorityClass = ProcessPriorityClass.BelowNormal;
                }
                catch
                {
                    // 忽略权限问题
                }

                PanoramaProtocol.Trace("工作进程启动, pid=" + Process.GetCurrentProcess().Id);
                try
                {
                    new PanoramaServer().Run();
                }
                catch (Exception ex)
                {
                    PanoramaProtocol.Trace("工作进程异常退出: " + ex);
                }
                PanoramaProtocol.Trace("工作进程退出");
            }
        }
    }
}
