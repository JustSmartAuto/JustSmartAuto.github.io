using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using Microsoft.Win32;
using Path = System.IO.Path;

namespace JusPatInspect
{
    /// <summary>
    /// PatInspect 图像差分检测工具主窗口
    /// 基于概率统计方法进行图像缺陷检测
    /// </summary>
    public partial class MainWindow : Window
    {
        #region 字段定义

        // 当前输入图像
        private WriteableBitmap _currentInputImage = null;
        
        // 训练模式图像 (平均值图像)
        private WriteableBitmap _trainedPattern = null;
        
        // 标准差图像
        private WriteableBitmap _stdDevImage = null;
        
        // 阈值图像
        private WriteableBitmap _thresholdImage = null;
        
        // Sobel边缘图像 (单图像训练时使用)
        private WriteableBitmap _sobelEdgeImage = null;
        
        // 训练图像集合 (用于统计训练)
        private List<WriteableBitmap> _trainingImages = new List<WriteableBitmap>();
        
        // 训练状态
        private bool _isTrained = false;
        private bool _isStatTraining = false;
        
        // 训练区域参数
        private double _regionOriginX = 0;
        private double _regionOriginY = 0;
        private double _regionWidth = 100;
        private double _regionHeight = 100;
        private double _regionRotation = 0;
        private double _regionSkew = 0;
        
        // 训练原点参数 (Pose)
        private double _poseTranslateX = 0;
        private double _poseTranslateY = 0;
        private double _poseScale = 1.0;
        private double _poseAspectRatio = 1.0;
        private double _poseRotation = 0;
        private double _poseSkew = 0;
        
        // 参数设置
        private double _sobelScale = 1.0;
        private double _sobelOffset = 0;
        private double _thresholdScale = 2.0;
        private double _thresholdOffset = 10;
        
        // 归一化模式
        private NormalizationMode _normalizationMode = NormalizationMode.HistogramEqualization;
        
        // 插值模式
        private InterpolationMode _interpolationMode = InterpolationMode.Bilinear;
        
        // 缺陷显示颜色
        private Color _defectColor = Colors.Red;
        private double _defectOpacity = 0.7;
        
        // 图像缩放和平移
        private double _zoomScale = 1.0;
        private Point _panOffset = new Point(0, 0);
        private bool _isPanning = false;
        private Point _lastMousePosition;
        
        // 批量处理取消令牌
        private CancellationTokenSource _batchCancellationTokenSource = null;

        #endregion

        #region 枚举定义

        /// <summary>
        /// 归一化模式枚举
        /// </summary>
        public enum NormalizationMode
        {
            Identity,                   // 无归一化
            HistogramEqualization,      // 直方图均衡化
            MeanAndStdDev,              // 均值和标准差
            MatchTails,                 // 匹配尾部
            RobustLineFit,              // 稳健线性拟合
            LocalCorrection,            // 局部校正
            EnhancedLocalCorrection     // 增强局部校正
        }

        /// <summary>
        /// 插值模式枚举
        /// </summary>
        public enum InterpolationMode
        {
            Bilinear,           // 双线性插值
            NearestNeighbor,    // 最近邻插值
            Bicubic             // 双三次插值
        }

        #endregion

        #region 构造函数

        /// <summary>
        /// 主窗口构造函数
        /// </summary>
        public MainWindow()
        {
            InitializeComponent();
            InitializeDefaults();
        }

        /// <summary>
        /// 初始化默认设置
        /// </summary>
        private void InitializeDefaults()
        {
            // 设置默认参数
            UpdateStatus("未训练", DarkRedBrush);
            UpdateTrainingCount(0);
        }

        #endregion

        #region 画刷资源

        private static readonly SolidColorBrush DarkRedBrush = new SolidColorBrush(Colors.DarkRed);
        private static readonly SolidColorBrush DarkGreenBrush = new SolidColorBrush(Colors.DarkGreen);
        private static readonly SolidColorBrush DarkBlueBrush = new SolidColorBrush(Colors.DarkBlue);

        #endregion

        #region 菜单事件处理

        /// <summary>
        /// 打开图像菜单
        /// </summary>
        private void MenuOpenImage_Click(object sender, RoutedEventArgs e) => BtnOpenImage_Click(sender, e);

        /// <summary>
        /// 保存图像菜单
        /// </summary>
        private void MenuSaveImage_Click(object sender, RoutedEventArgs e) => BtnSaveImage_Click(sender, e);

        /// <summary>
        /// 打开文件夹菜单
        /// </summary>
        private void MenuOpenFolder_Click(object sender, RoutedEventArgs e) => BtnOpenFolder_Click(sender, e);

        /// <summary>
        /// 退出菜单
        /// </summary>
        private void MenuExit_Click(object sender, RoutedEventArgs e) => Close();

        /// <summary>
        /// 训练新模式菜单
        /// </summary>
        private void MenuTrainNew_Click(object sender, RoutedEventArgs e) => BtnTrainNew_Click(sender, e);

        /// <summary>
        /// 统计训练菜单
        /// </summary>
        private void MenuStatTrain_Click(object sender, RoutedEventArgs e) => BtnStatTrain_Click(sender, e);

        /// <summary>
        /// 结束统计训练菜单
        /// </summary>
        private void MenuEndStatTrain_Click(object sender, RoutedEventArgs e) => BtnEndStatTrain_Click(sender, e);

        /// <summary>
        /// 取消训练菜单
        /// </summary>
        private void MenuCancelTrain_Click(object sender, RoutedEventArgs e) => BtnCancelTrain_Click(sender, e);

        /// <summary>
        /// 加载模式菜单
        /// </summary>
        private void MenuLoadPattern_Click(object sender, RoutedEventArgs e) => BtnLoadPattern_Click(sender, e);

        /// <summary>
        /// 保存模式菜单
        /// </summary>
        private void MenuSavePattern_Click(object sender, RoutedEventArgs e) => BtnSavePattern_Click(sender, e);

        /// <summary>
        /// 运行检测菜单
        /// </summary>
        private void MenuRun_Click(object sender, RoutedEventArgs e) => BtnRun_Click(sender, e);

        /// <summary>
        /// 批量检测菜单
        /// </summary>
        private void MenuRunFolder_Click(object sender, RoutedEventArgs e)
        {
            MainTabControl.SelectedIndex = 4; // 切换到批量处理Tab
        }

        /// <summary>
        /// 关于菜单
        /// </summary>
        private void MenuAbout_Click(object sender, RoutedEventArgs e)
        {
            MessageBox.Show(
                "PatInspect - 基于概率统计的图像差分检测工具\n\n" +
                "版本: 1.0\n" +
                "功能: 通过统计训练建立标准模式，检测图像缺陷\n\n" +
                "原理: 比较输入图像与训练模式的差异，\n" +
                "      通过阈值处理识别潜在缺陷区域。",
                "关于",
                MessageBoxButton.OK,
                MessageBoxImage.Information);
        }

        #endregion

        #region 工具栏按钮事件

        /// <summary>
        /// 运行检测按钮
        /// </summary>
        private void BtnRun_Click(object sender, RoutedEventArgs e)
        {
            if (!_isTrained)
            {
                MessageBox.Show("请先训练模式图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (_currentInputImage == null)
            {
                MessageBox.Show("请先加载输入图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            try
            {
                var startTime = DateTime.Now;
                
                // 执行检测
                PerformInspection();
                
                var elapsed = (DateTime.Now - startTime).TotalMilliseconds;
                TimeText.Text = $"{elapsed:F4}毫秒";
                MessageText.Text = "检测完成";
            }
            catch (Exception ex)
            {
                MessageBox.Show($"检测失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
                MessageText.Text = "检测失败";
            }
        }

        /// <summary>
        /// 训练按钮
        /// </summary>
        private void BtnTrain_Click(object sender, RoutedEventArgs e) => BtnTrainNew_Click(sender, e);

        /// <summary>
        /// 打开图像按钮
        /// </summary>
        private void BtnOpenImage_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Filter = "图像文件|*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff|所有文件|*.*",
                Title = "打开图像"
            };

            if (dialog.ShowDialog() == true)
            {
                try
                {
                    LoadImage(dialog.FileName);
                    MessageText.Text = $"已加载: {System.IO.Path.GetFileName(dialog.FileName)}";
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"加载图像失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
        }

        /// <summary>
        /// 保存图像按钮
        /// </summary>
        private void BtnSaveImage_Click(object sender, RoutedEventArgs e)
        {
            if (_currentInputImage == null)
            {
                MessageBox.Show("没有可保存的图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var dialog = new SaveFileDialog
            {
                Filter = "PNG图像|*.png|JPEG图像|*.jpg|BMP图像|*.bmp",
                Title = "保存图像",
                DefaultExt = "png"
            };

            if (dialog.ShowDialog() == true)
            {
                try
                {
                    SaveBitmap(_currentInputImage, dialog.FileName);
                    MessageText.Text = $"已保存: {System.IO.Path.GetFileName(dialog.FileName)}";
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"保存图像失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
        }

        /// <summary>
        /// 打开文件夹按钮
        /// </summary>
        private void BtnOpenFolder_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFolderDialog
            {
                Title = "选择包含图像的文件夹"
            };

            if (dialog.ShowDialog() == true)
            {
                var files = Directory.GetFiles(dialog.FolderName, "*.png")
                    .Concat(Directory.GetFiles(dialog.FolderName, "*.jpg"))
                    .Concat(Directory.GetFiles(dialog.FolderName, "*.jpeg"))
                    .Concat(Directory.GetFiles(dialog.FolderName, "*.bmp"))
                    .ToArray();

                if (files.Length > 0)
                {
                    LoadImage(files[0]);
                    MessageText.Text = $"文件夹共 {files.Length} 张图像";
                }
                else
                {
                    MessageBox.Show("文件夹中没有支持的图像文件！", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                }
            }
        }

        /// <summary>
        /// 撤销按钮
        /// </summary>
        private void BtnUndo_Click(object sender, RoutedEventArgs e)
        {
            MessageText.Text = "撤销功能待实现";
        }

        /// <summary>
        /// 重做按钮
        /// </summary>
        private void BtnRedo_Click(object sender, RoutedEventArgs e)
        {
            MessageText.Text = "重做功能待实现";
        }

        /// <summary>
        /// 放大按钮
        /// </summary>
        private void BtnZoomIn_Click(object sender, RoutedEventArgs e)
        {
            _zoomScale *= 1.2;
            ApplyZoom();
        }

        /// <summary>
        /// 缩小按钮
        /// </summary>
        private void BtnZoomOut_Click(object sender, RoutedEventArgs e)
        {
            _zoomScale /= 1.2;
            if (_zoomScale < 0.1) _zoomScale = 0.1;
            ApplyZoom();
        }

        /// <summary>
        /// 适应窗口按钮
        /// </summary>
        private void BtnFit_Click(object sender, RoutedEventArgs e)
        {
            _zoomScale = 1.0;
            _panOffset = new Point(0, 0);
            ApplyZoom();
        }

        /// <summary>
        /// 帮助按钮
        /// </summary>
        private void BtnHelp_Click(object sender, RoutedEventArgs e)
        {
            MenuAbout_Click(sender, e);
        }

        #endregion

        #region 训练参数Tab事件

        /// <summary>
        /// Sobel缩放文本改变
        /// </summary>
        private void SobelScaleText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(SobelScaleText.Text, out double value))
            {
                _sobelScale = value;
                if (_isTrained && _trainingImages.Count == 1)
                {
                    // 重新生成Sobel边缘图像
                    GenerateSobelEdgeImage();
                }
            }
        }

        /// <summary>
        /// Sobel偏移文本改变
        /// </summary>
        private void SobelOffsetText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(SobelOffsetText.Text, out double value))
            {
                _sobelOffset = value;
            }
        }

        /// <summary>
        /// Sobel缩放增加
        /// </summary>
        private void SobelScaleUp_Click(object sender, RoutedEventArgs e)
        {
            _sobelScale += 0.1;
            SobelScaleText.Text = _sobelScale.ToString("F1");
        }

        /// <summary>
        /// Sobel缩放减少
        /// </summary>
        private void SobelScaleDown_Click(object sender, RoutedEventArgs e)
        {
            _sobelScale = Math.Max(0.1, _sobelScale - 0.1);
            SobelScaleText.Text = _sobelScale.ToString("F1");
        }

        /// <summary>
        /// Sobel偏移增加
        /// </summary>
        private void SobelOffsetUp_Click(object sender, RoutedEventArgs e)
        {
            _sobelOffset += 1;
            SobelOffsetText.Text = _sobelOffset.ToString("F0");
        }

        /// <summary>
        /// Sobel偏移减少
        /// </summary>
        private void SobelOffsetDown_Click(object sender, RoutedEventArgs e)
        {
            _sobelOffset = Math.Max(0, _sobelOffset - 1);
            SobelOffsetText.Text = _sobelOffset.ToString("F0");
        }

        /// <summary>
        /// 阈值比例文本改变
        /// </summary>
        private void ThresholdScaleText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(ThresholdScaleText.Text, out double value))
            {
                _thresholdScale = value;
                if (_isTrained)
                {
                    GenerateThresholdImage();
                }
            }
        }

        /// <summary>
        /// 阈值偏移文本改变
        /// </summary>
        private void ThresholdOffsetText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(ThresholdOffsetText.Text, out double value))
            {
                _thresholdOffset = value;
                if (_isTrained)
                {
                    GenerateThresholdImage();
                }
            }
        }

        /// <summary>
        /// 阈值比例增加
        /// </summary>
        private void ThresholdScaleUp_Click(object sender, RoutedEventArgs e)
        {
            _thresholdScale += 0.5;
            ThresholdScaleText.Text = _thresholdScale.ToString("F1");
        }

        /// <summary>
        /// 阈值比例减少
        /// </summary>
        private void ThresholdScaleDown_Click(object sender, RoutedEventArgs e)
        {
            _thresholdScale = Math.Max(0.1, _thresholdScale - 0.5);
            ThresholdScaleText.Text = _thresholdScale.ToString("F1");
        }

        /// <summary>
        /// 阈值偏移增加
        /// </summary>
        private void ThresholdOffsetUp_Click(object sender, RoutedEventArgs e)
        {
            _thresholdOffset += 5;
            ThresholdOffsetText.Text = _thresholdOffset.ToString("F0");
        }

        /// <summary>
        /// 阈值偏移减少
        /// </summary>
        private void ThresholdOffsetDown_Click(object sender, RoutedEventArgs e)
        {
            _thresholdOffset = Math.Max(0, _thresholdOffset - 5);
            ThresholdOffsetText.Text = _thresholdOffset.ToString("F0");
        }

        /// <summary>
        /// 插值模式改变
        /// </summary>
        private void InterpolationCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            _interpolationMode = (InterpolationMode)InterpolationCombo.SelectedIndex;
        }

        /// <summary>
        /// 抓取训练图像与原点
        /// </summary>
        private void BtnGrabTrainImage_Click(object sender, RoutedEventArgs e)
        {
            if (_currentInputImage == null)
            {
                MessageBox.Show("请先加载输入图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            // 更新训练区域为当前图像中心
            _regionOriginX = _currentInputImage.PixelWidth / 2.0;
            _regionOriginY = _currentInputImage.PixelHeight / 2.0;
            _regionWidth = _currentInputImage.PixelWidth * 0.8;
            _regionHeight = _currentInputImage.PixelHeight * 0.8;
            
            UpdateRegionUI();
            MessageText.Text = "已抓取训练图像与原点";
        }

        /// <summary>
        /// 训练新模式
        /// </summary>
        private void BtnTrainNew_Click(object sender, RoutedEventArgs e)
        {
            if (_currentInputImage == null)
            {
                MessageBox.Show("请先加载输入图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            try
            {
                // 清除之前的训练数据
                _trainingImages.Clear();
                
                // 添加当前图像作为第一张训练图像
                _trainingImages.Add(CloneBitmap(_currentInputImage));
                
                // 创建训练模式
                CreateTrainedPattern();
                
                // 更新状态
                _isTrained = true;
                UpdateStatus("已训练 (单图像)", DarkGreenBrush);
                UpdateTrainingCount(1);
                
                // 启用统计训练按钮
                BtnStatTrain.IsEnabled = true;
                BtnCancelTrain.IsEnabled = true;
                MenuStatTrain.IsEnabled = true;
                MenuCancelTrain.IsEnabled = true;
                
                // 显示训练模式图像
                PatternImage.Source = _trainedPattern;
                
                MessageText.Text = "训练新模式完成 (单图像训练)";
            }
            catch (Exception ex)
            {
                MessageBox.Show($"训练失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        /// <summary>
        /// 统计训练当前模式
        /// </summary>
        private void BtnStatTrain_Click(object sender, RoutedEventArgs e)
        {
            if (_currentInputImage == null)
            {
                MessageBox.Show("请先加载输入图像！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (!_isStatTraining)
            {
                _isStatTraining = true;
                BtnStatTrain.Content = "添加训练图像";
                BtnEndStatTrain.IsEnabled = true;
                MenuEndStatTrain.IsEnabled = true;
                MessageText.Text = "开始统计训练，请添加更多训练图像";
            }

            // 添加当前图像到训练集
            _trainingImages.Add(CloneBitmap(_currentInputImage));
            
            // 更新训练模式
            CreateTrainedPattern();
            
            UpdateTrainingCount(_trainingImages.Count);
            UpdateStatus($"统计训练中 ({_trainingImages.Count}张)", DarkBlueBrush);
            MessageText.Text = $"已添加训练图像 #{_trainingImages.Count}";
        }

        /// <summary>
        /// 结束统计训练
        /// </summary>
        private void BtnEndStatTrain_Click(object sender, RoutedEventArgs e)
        {
            _isStatTraining = false;
            BtnStatTrain.Content = "统计训练当前模式";
            BtnEndStatTrain.IsEnabled = false;
            MenuEndStatTrain.IsEnabled = false;
            
            UpdateStatus($"已训练 ({_trainingImages.Count}张图像)", DarkGreenBrush);
            MessageText.Text = "统计训练完成";
        }

        /// <summary>
        /// 取消训练模式
        /// </summary>
        private void BtnCancelTrain_Click(object sender, RoutedEventArgs e)
        {
            _trainingImages.Clear();
            _trainedPattern = null;
            _stdDevImage = null;
            _thresholdImage = null;
            _sobelEdgeImage = null;
            _isTrained = false;
            _isStatTraining = false;
            
            BtnStatTrain.Content = "统计训练当前模式";
            BtnStatTrain.IsEnabled = false;
            BtnEndStatTrain.IsEnabled = false;
            BtnCancelTrain.IsEnabled = false;
            MenuStatTrain.IsEnabled = false;
            MenuEndStatTrain.IsEnabled = false;
            MenuCancelTrain.IsEnabled = false;
            
            PatternImage.Source = null;
            UpdateStatus("未训练", DarkRedBrush);
            UpdateTrainingCount(0);
            MessageText.Text = "已取消训练模式";
        }

        /// <summary>
        /// 加载模式
        /// </summary>
        private void BtnLoadPattern_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Filter = "模式文件|*.pat|所有文件|*.*",
                Title = "加载训练模式"
            };

            if (dialog.ShowDialog() == true)
            {
                try
                {
                    LoadPattern(dialog.FileName);
                    MessageText.Text = "模式加载成功";
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"加载模式失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
        }

        /// <summary>
        /// 保存模式
        /// </summary>
        private void BtnSavePattern_Click(object sender, RoutedEventArgs e)
        {
            if (!_isTrained)
            {
                MessageBox.Show("没有可保存的训练模式！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var dialog = new SaveFileDialog
            {
                Filter = "模式文件|*.pat",
                Title = "保存训练模式",
                DefaultExt = "pat"
            };

            if (dialog.ShowDialog() == true)
            {
                try
                {
                    SavePattern(dialog.FileName);
                    MessageText.Text = "模式保存成功";
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"保存模式失败: {ex.Message}", "错误", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
        }

        #endregion

        #region 训练区域与原点Tab事件

        private void RegionModeCombo_SelectionChanged(object sender, SelectionChangedEventArgs e) { }
        private void RegionShapeCombo_SelectionChanged(object sender, SelectionChangedEventArgs e) { }
        private void SelectMode_Checked(object sender, RoutedEventArgs e) { }
        private void OriginXText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(OriginXText.Text, out double value)) _regionOriginX = value;
        }
        private void OriginYText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(OriginYText.Text, out double value)) _regionOriginY = value;
        }
        private void SideXText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(SideXText.Text, out double value)) _regionWidth = value;
        }
        private void SideYText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(SideYText.Text, out double value)) _regionHeight = value;
        }
        private void RotationText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(RotationText.Text, out double value)) _regionRotation = value;
        }
        private void SkewText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(SkewText.Text, out double value)) _regionSkew = value;
        }
        private void TranslateXText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(TranslateXText.Text, out double value)) _poseTranslateX = value;
        }
        private void TranslateYText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(TranslateYText.Text, out double value)) _poseTranslateY = value;
        }
        private void ScaleText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(ScaleText.Text, out double value)) _poseScale = value;
        }
        private void AspectRatioText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(AspectRatioText.Text, out double value)) _poseAspectRatio = value;
        }
        private void TrainRotationText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(TrainRotationText.Text, out double value)) _poseRotation = value;
        }
        private void TrainSkewText_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (double.TryParse(TrainSkewText.Text, out double value)) _poseSkew = value;
        }

        /// <summary>
        /// 适应图像
        /// </summary>
        private void BtnFitToImage_Click(object sender, RoutedEventArgs e)
        {
            if (_currentInputImage != null)
            {
                _regionOriginX = _currentInputImage.PixelWidth / 2.0;
                _regionOriginY = _currentInputImage.PixelHeight / 2.0;
                _regionWidth = _currentInputImage.PixelWidth;
                _regionHeight = _currentInputImage.PixelHeight;
                _regionRotation = 0;
                _regionSkew = 0;
                UpdateRegionUI();
            }
        }

        #endregion

        #region 运行参数Tab事件

        /// <summary>
        /// 归一化模式改变
        /// </summary>
        private void NormalizeModeCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            _normalizationMode = (NormalizationMode)NormalizeModeCombo.SelectedIndex;
            
            // 显示/隐藏局部校正参数
            if (_normalizationMode == NormalizationMode.LocalCorrection ||
                _normalizationMode == NormalizationMode.EnhancedLocalCorrection)
            {
                LocalCorrectionParams.Visibility = Visibility.Visible;
            }
            else
            {
                LocalCorrectionParams.Visibility = Visibility.Collapsed;
            }
        }

        #endregion

        #region 图形Tab事件

        private void ShowOption_CheckedChanged(object sender, RoutedEventArgs e) => UpdateGraphicsOverlay();
        private void DiagOption_CheckedChanged(object sender, RoutedEventArgs e) => UpdateGraphicsOverlay();

        /// <summary>
        /// 缺陷颜色改变
        /// </summary>
        private void DefectColorCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            switch (DefectColorCombo.SelectedIndex)
            {
                case 0: _defectColor = Colors.Red; break;
                case 1: _defectColor = Colors.Green; break;
                case 2: _defectColor = Colors.Blue; break;
                case 3: _defectColor = Colors.Yellow; break;
                case 4: _defectColor = Colors.White; break;
            }
            UpdateGraphicsOverlay();
        }

        /// <summary>
        /// 缺陷透明度改变
        /// </summary>
        private void DefectOpacitySlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            _defectOpacity = DefectOpacitySlider.Value;
            UpdateGraphicsOverlay();
        }

        #endregion

        #region 批量处理Tab事件

        /// <summary>
        /// 浏览输入文件夹
        /// </summary>
        private void BtnBrowseInput_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFolderDialog
            {
                Title = "选择输入图像文件夹"
            };

            if (dialog.ShowDialog() == true)
            {
                BatchInputFolderText.Text = dialog.FolderName;
            }
        }

        /// <summary>
        /// 浏览输出文件夹
        /// </summary>
        private void BtnBrowseOutput_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFolderDialog
            {
                Title = "选择输出结果文件夹"
            };

            if (dialog.ShowDialog() == true)
            {
                BatchOutputFolderText.Text = dialog.FolderName;
            }
        }

        /// <summary>
        /// 开始批量处理
        /// </summary>
        private async void BtnStartBatch_Click(object sender, RoutedEventArgs e)
        {
            if (!_isTrained)
            {
                MessageBox.Show("请先训练模式！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (string.IsNullOrEmpty(BatchInputFolderText.Text))
            {
                MessageBox.Show("请选择输入文件夹！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (string.IsNullOrEmpty(BatchOutputFolderText.Text))
            {
                MessageBox.Show("请选择输出文件夹！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var imageFiles = Directory.GetFiles(BatchInputFolderText.Text, "*.png")
                .Concat(Directory.GetFiles(BatchInputFolderText.Text, "*.jpg"))
                .Concat(Directory.GetFiles(BatchInputFolderText.Text, "*.jpeg"))
                .Concat(Directory.GetFiles(BatchInputFolderText.Text, "*.bmp"))
                .ToArray();

            if (imageFiles.Length == 0)
            {
                MessageBox.Show("输入文件夹中没有图像文件！", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            BtnStartBatch.IsEnabled = false;
            BtnStopBatch.IsEnabled = true;
            _batchCancellationTokenSource = new CancellationTokenSource();

            var results = new List<BatchResult>();
            int maxDefectPixels = int.TryParse(MaxDefectPixelsText.Text, out int mp) ? mp : 1000;
            double maxDefectRatio = double.TryParse(MaxDefectRatioText.Text.Replace("%", ""), out double mr) ? mr : 1.0;

            try
            {
                for (int i = 0; i < imageFiles.Length; i++)
                {
                    if (_batchCancellationTokenSource.Token.IsCancellationRequested)
                        break;

                    var file = imageFiles[i];
                    var progress = (int)((i + 1.0) / imageFiles.Length * 100);
                    
                    await Task.Run(() =>
                    {
                        Dispatcher.Invoke(() =>
                        {
                            BatchProgressBar.Value = progress;
                            BatchStatusText.Text = $"处理中... ({i + 1}/{imageFiles.Length}) {System.IO.Path.GetFileName(file)}";
                        });

                        try
                        {
                            var result = ProcessSingleImage(file, maxDefectPixels, maxDefectRatio);
                            Dispatcher.Invoke(() => results.Add(result));
                        }
                        catch (Exception ex)
                        {
                            Dispatcher.Invoke(() => results.Add(new BatchResult
                            {
                                FileName = System.IO.Path.GetFileName(file),
                                Result = $"错误: {ex.Message}",
                                ProcessTime = "0ms"
                            }));
                        }
                    });
                }

                // 保存报告
                if (SaveReportCheck.IsChecked == true)
                {
                    SaveBatchReport(results);
                }

                BatchStatusText.Text = $"处理完成，共 {imageFiles.Length} 张图像";
            }
            catch (Exception ex)
            {
                BatchStatusText.Text = $"处理失败: {ex.Message}";
            }
            finally
            {
                BtnStartBatch.IsEnabled = true;
                BtnStopBatch.IsEnabled = false;
                _batchCancellationTokenSource = null;
            }

            BatchResultGrid.ItemsSource = results;
        }

        /// <summary>
        /// 停止批量处理
        /// </summary>
        private void BtnStopBatch_Click(object sender, RoutedEventArgs e)
        {
            _batchCancellationTokenSource?.Cancel();
            BatchStatusText.Text = "正在停止...";
        }

        #endregion

        #region 图像交互事件

        /// <summary>
        /// 鼠标滚轮缩放
        /// </summary>
        private void Image_MouseWheel(object sender, MouseWheelEventArgs e)
        {
            if (e.Delta > 0)
                _zoomScale *= 1.1;
            else
                _zoomScale /= 1.1;

            if (_zoomScale < 0.1) _zoomScale = 0.1;
            if (_zoomScale > 10) _zoomScale = 10;

            ApplyZoom();
            e.Handled = true;
        }

        /// <summary>
        /// 鼠标左键按下
        /// </summary>
        private void Image_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            _isPanning = true;
            _lastMousePosition = e.GetPosition(this);
            ((Image)sender).CaptureMouse();
        }

        /// <summary>
        /// 鼠标移动
        /// </summary>
        private void Image_MouseMove(object sender, MouseEventArgs e)
        {
            if (_isPanning)
            {
                var currentPosition = e.GetPosition(this);
                var delta = currentPosition - _lastMousePosition;
                _panOffset.X += delta.X;
                _panOffset.Y += delta.Y;
                _lastMousePosition = currentPosition;
                ApplyZoom();
            }
        }

        /// <summary>
        /// 鼠标左键释放
        /// </summary>
        private void Image_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
        {
            _isPanning = false;
            ((Image)sender).ReleaseMouseCapture();
        }

        /// <summary>
        /// Tab切换事件
        /// </summary>
        private void MainTabControl_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            // 切换Tab时更新显示
            if (MainTabControl.SelectedIndex == 3) // 图形Tab
            {
                UpdateGraphicsOverlay();
            }
        }

        #endregion

        #region 核心图像处理算法

        /// <summary>
        /// 加载图像文件
        /// </summary>
        private void LoadImage(string filePath)
        {
            var bitmap = new BitmapImage(new Uri(filePath));
            _currentInputImage = ConvertToWriteableBitmap(bitmap);
            
            // 更新所有图像显示
            TrainParamImage.Source = _currentInputImage;
            RegionImage.Source = _currentInputImage;
            RunParamImage.Source = _currentInputImage;
            GraphicsImage.Source = _currentInputImage;
            
            // 更新图像信息
            ImageInfoText.Text = $"图像: {bitmap.PixelWidth}x{bitmap.PixelHeight}";
            
            // 重置缩放
            _zoomScale = 1.0;
            _panOffset = new Point(0, 0);
            ApplyZoom();
        }

        /// <summary>
        /// 将BitmapImage转换为WriteableBitmap
        /// </summary>
        private WriteableBitmap ConvertToWriteableBitmap(BitmapImage bitmapImage)
        {
            var writeableBitmap = new WriteableBitmap(bitmapImage);
            return writeableBitmap;
        }

        /// <summary>
        /// 克隆位图
        /// </summary>
        private WriteableBitmap CloneBitmap(WriteableBitmap source)
        {
            var clone = new WriteableBitmap(source.PixelWidth, source.PixelHeight, 
                source.DpiX, source.DpiY, source.Format, source.Palette);
            
            var pixels = new byte[source.PixelWidth * source.PixelHeight * 4];
            source.CopyPixels(pixels, source.PixelWidth * 4, 0);
            clone.WritePixels(new Int32Rect(0, 0, source.PixelWidth, source.PixelHeight), 
                pixels, source.PixelWidth * 4, 0);
            
            return clone;
        }

        /// <summary>
        /// 创建训练模式 (统计平均)
        /// </summary>
        private void CreateTrainedPattern()
        {
            if (_trainingImages.Count == 0) return;

            int width = _trainingImages[0].PixelWidth;
            int height = _trainingImages[0].PixelHeight;
            int stride = width * 4;

            // 创建平均值图像和标准差图像
            _trainedPattern = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            _stdDevImage = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);

            var meanPixels = new double[width * height * 4];
            var variancePixels = new double[width * height * 4];

            // 计算平均值
            foreach (var image in _trainingImages)
            {
                var pixels = new byte[stride * height];
                image.CopyPixels(pixels, stride, 0);

                for (int i = 0; i < pixels.Length; i++)
                {
                    meanPixels[i] += pixels[i];
                }
            }

            for (int i = 0; i < meanPixels.Length; i++)
            {
                meanPixels[i] /= _trainingImages.Count;
            }

            // 计算方差
            foreach (var image in _trainingImages)
            {
                var pixels = new byte[stride * height];
                image.CopyPixels(pixels, stride, 0);

                for (int i = 0; i < pixels.Length; i++)
                {
                    double diff = pixels[i] - meanPixels[i];
                    variancePixels[i] += diff * diff;
                }
            }

            // 生成训练模式图像和标准差图像
            var patternPixels = new byte[stride * height];
            var stdDevPixels = new byte[stride * height];

            for (int i = 0; i < meanPixels.Length; i++)
            {
                patternPixels[i] = (byte)Math.Clamp(meanPixels[i], 0, 255);
                
                double variance = variancePixels[i] / _trainingImages.Count;
                double stdDev = Math.Sqrt(variance);
                stdDevPixels[i] = (byte)Math.Clamp(stdDev, 0, 255);
            }

            _trainedPattern.WritePixels(new Int32Rect(0, 0, width, height), patternPixels, stride, 0);
            _stdDevImage.WritePixels(new Int32Rect(0, 0, width, height), stdDevPixels, stride, 0);

            // 如果是单图像训练，生成Sobel边缘图像
            if (_trainingImages.Count == 1)
            {
                GenerateSobelEdgeImage();
            }

            // 生成阈值图像
            GenerateThresholdImage();
        }

        /// <summary>
        /// 生成Sobel边缘图像 (单图像训练时使用)
        /// </summary>
        private void GenerateSobelEdgeImage()
        {
            if (_trainedPattern == null) return;

            int width = _trainedPattern.PixelWidth;
            int height = _trainedPattern.PixelHeight;
            int stride = width * 4;

            var pixels = new byte[stride * height];
            _trainedPattern.CopyPixels(pixels, stride, 0);

            var grayImage = new byte[width * height];
            
            // 转换为灰度图
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int idx = y * stride + x * 4;
                    grayImage[y * width + x] = (byte)((pixels[idx] + pixels[idx + 1] + pixels[idx + 2]) / 3);
                }
            }

            // Sobel边缘检测
            var edgeImage = new byte[width * height];
            int[] sobelX = { -1, 0, 1, -2, 0, 2, -1, 0, 1 };
            int[] sobelY = { -1, -2, -1, 0, 0, 0, 1, 2, 1 };

            for (int y = 1; y < height - 1; y++)
            {
                for (int x = 1; x < width - 1; x++)
                {
                    double gx = 0, gy = 0;
                    
                    for (int ky = -1; ky <= 1; ky++)
                    {
                        for (int kx = -1; kx <= 1; kx++)
                        {
                            int pixel = grayImage[(y + ky) * width + (x + kx)];
                            gx += pixel * sobelX[(ky + 1) * 3 + (kx + 1)];
                            gy += pixel * sobelY[(ky + 1) * 3 + (kx + 1)];
                        }
                    }

                    double magnitude = Math.Sqrt(gx * gx + gy * gy);
                    edgeImage[y * width + x] = (byte)Math.Clamp(magnitude * _sobelScale + _sobelOffset, 0, 255);
                }
            }

            // 转换为彩色图像
            _sobelEdgeImage = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            var edgePixels = new byte[stride * height];

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int idx = y * stride + x * 4;
                    byte edgeValue = edgeImage[y * width + x];
                    edgePixels[idx] = edgeValue;     // B
                    edgePixels[idx + 1] = edgeValue; // G
                    edgePixels[idx + 2] = edgeValue; // R
                    edgePixels[idx + 3] = 255;       // A
                }
            }

            _sobelEdgeImage.WritePixels(new Int32Rect(0, 0, width, height), edgePixels, stride, 0);
        }

        /// <summary>
        /// 生成阈值图像
        /// </summary>
        private void GenerateThresholdImage()
        {
            if (_stdDevImage == null && _sobelEdgeImage == null) return;

            int width = _trainedPattern.PixelWidth;
            int height = _trainedPattern.PixelHeight;
            int stride = width * 4;

            _thresholdImage = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            var thresholdPixels = new byte[stride * height];

            if (_trainingImages.Count > 1)
            {
                // 统计训练: 使用标准差图像
                var stdDevPixels = new byte[stride * height];
                _stdDevImage.CopyPixels(stdDevPixels, stride, 0);

                for (int i = 0; i < stdDevPixels.Length; i += 4)
                {
                    double avgStdDev = (stdDevPixels[i] + stdDevPixels[i + 1] + stdDevPixels[i + 2]) / 3.0;
                    byte thresholdValue = (byte)Math.Clamp(_thresholdScale * avgStdDev + _thresholdOffset, 0, 255);
                    
                    thresholdPixels[i] = thresholdValue;
                    thresholdPixels[i + 1] = thresholdValue;
                    thresholdPixels[i + 2] = thresholdValue;
                    thresholdPixels[i + 3] = 255;
                }
            }
            else
            {
                // 单图像训练: 使用Sobel边缘图像
                var sobelPixels = new byte[stride * height];
                _sobelEdgeImage.CopyPixels(sobelPixels, stride, 0);

                for (int i = 0; i < sobelPixels.Length; i += 4)
                {
                    double avgEdge = (sobelPixels[i] + sobelPixels[i + 1] + sobelPixels[i + 2]) / 3.0;
                    byte thresholdValue = (byte)Math.Clamp(_thresholdScale * avgEdge + _thresholdOffset, 0, 255);
                    
                    thresholdPixels[i] = thresholdValue;
                    thresholdPixels[i + 1] = thresholdValue;
                    thresholdPixels[i + 2] = thresholdValue;
                    thresholdPixels[i + 3] = 255;
                }
            }

            _thresholdImage.WritePixels(new Int32Rect(0, 0, width, height), thresholdPixels, stride, 0);
        }

        /// <summary>
        /// 执行检测
        /// </summary>
        private void PerformInspection()
        {
            if (_currentInputImage == null || _trainedPattern == null) return;

            int width = _currentInputImage.PixelWidth;
            int height = _currentInputImage.PixelHeight;
            int stride = width * 4;

            // 获取输入图像像素
            var inputPixels = new byte[stride * height];
            _currentInputImage.CopyPixels(inputPixels, stride, 0);

            // 归一化处理
            byte[] normalizedPixels = NormalizeImage(inputPixels, width, height);

            // 获取训练模式像素
            var patternPixels = new byte[stride * height];
            _trainedPattern.CopyPixels(patternPixels, stride, 0);

            // 获取阈值图像像素
            var thresholdPixels = new byte[stride * height];
            _thresholdImage.CopyPixels(thresholdPixels, stride, 0);

            // 计算原始差异图像
            var rawDiffPixels = new byte[stride * height];
            var thresholdDiffPixels = new byte[stride * height];

            int defectPixelCount = 0;
            double totalDefectIntensity = 0;
            int maxDefectIntensity = 0;

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int idx = y * stride + x * 4;

                    // 计算绝对差异
                    int diffB = Math.Abs(normalizedPixels[idx] - patternPixels[idx]);
                    int diffG = Math.Abs(normalizedPixels[idx + 1] - patternPixels[idx + 1]);
                    int diffR = Math.Abs(normalizedPixels[idx + 2] - patternPixels[idx + 2]);
                    int avgDiff = (diffB + diffG + diffR) / 3;

                    // 原始差异图像
                    rawDiffPixels[idx] = (byte)avgDiff;
                    rawDiffPixels[idx + 1] = (byte)avgDiff;
                    rawDiffPixels[idx + 2] = (byte)avgDiff;
                    rawDiffPixels[idx + 3] = 255;

                    // 阈值处理
                    int threshold = (thresholdPixels[idx] + thresholdPixels[idx + 1] + thresholdPixels[idx + 2]) / 3;
                    
                    if (avgDiff > threshold)
                    {
                        int defectValue = Math.Max(0, avgDiff - (int)_thresholdOffset);
                        thresholdDiffPixels[idx] = (byte)defectValue;
                        thresholdDiffPixels[idx + 1] = (byte)defectValue;
                        thresholdDiffPixels[idx + 2] = (byte)defectValue;
                        thresholdDiffPixels[idx + 3] = 255;

                        defectPixelCount++;
                        totalDefectIntensity += defectValue;
                        maxDefectIntensity = Math.Max(maxDefectIntensity, defectValue);
                    }
                    else
                    {
                        thresholdDiffPixels[idx] = 0;
                        thresholdDiffPixels[idx + 1] = 0;
                        thresholdDiffPixels[idx + 2] = 0;
                        thresholdDiffPixels[idx + 3] = 255;
                    }
                }
            }

            // 创建差异图像
            var rawDiffBitmap = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            rawDiffBitmap.WritePixels(new Int32Rect(0, 0, width, height), rawDiffPixels, stride, 0);
            RawDiffImage.Source = rawDiffBitmap;

            var thresholdDiffBitmap = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            thresholdDiffBitmap.WritePixels(new Int32Rect(0, 0, width, height), thresholdDiffPixels, stride, 0);
            ThresholdDiffImage.Source = thresholdDiffBitmap;

            // 创建匹配图像
            var matchBitmap = new WriteableBitmap(width, height, 96, 96, PixelFormats.Bgra32, null);
            matchBitmap.WritePixels(new Int32Rect(0, 0, width, height), normalizedPixels, stride, 0);
            MatchImage.Source = matchBitmap;

            // 更新缺陷统计
            double defectRatio = (double)defectPixelCount / (width * height) * 100;
            double avgDefectIntensity = defectPixelCount > 0 ? totalDefectIntensity / defectPixelCount : 0;

            DefectPixelCountText.Text = defectPixelCount.ToString();
            DefectRatioText.Text = $"{defectRatio:F2}%";
            AvgDefectIntensityText.Text = $"{avgDefectIntensity:F1}";
            MaxDefectIntensityText.Text = maxDefectIntensity.ToString();
        }

        /// <summary>
        /// 图像归一化
        /// </summary>
        private byte[] NormalizeImage(byte[] inputPixels, int width, int height)
        {
            if (_normalizationMode == NormalizationMode.Identity)
                return inputPixels;

            int stride = width * 4;
            var outputPixels = new byte[inputPixels.Length];
            Array.Copy(inputPixels, outputPixels, inputPixels.Length);

            switch (_normalizationMode)
            {
                case NormalizationMode.HistogramEqualization:
                    outputPixels = HistogramEqualization(inputPixels, width, height);
                    break;
                case NormalizationMode.MeanAndStdDev:
                    outputPixels = MeanStdDevNormalization(inputPixels, width, height);
                    break;
                case NormalizationMode.MatchTails:
                    outputPixels = MatchTailsNormalization(inputPixels, width, height);
                    break;
                case NormalizationMode.LocalCorrection:
                case NormalizationMode.EnhancedLocalCorrection:
                    outputPixels = LocalCorrectionNormalization(inputPixels, width, height);
                    break;
            }

            return outputPixels;
        }

        /// <summary>
        /// 直方图均衡化
        /// </summary>
        private byte[] HistogramEqualization(byte[] pixels, int width, int height)
        {
            int[] histogram = new int[256];
            int totalPixels = width * height;

            // 计算灰度直方图
            for (int i = 0; i < pixels.Length; i += 4)
            {
                int gray = (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3;
                histogram[gray]++;
            }

            // 计算累积分布函数
            int[] cdf = new int[256];
            cdf[0] = histogram[0];
            for (int i = 1; i < 256; i++)
            {
                cdf[i] = cdf[i - 1] + histogram[i];
            }

            // 归一化CDF
            byte[] lookupTable = new byte[256];
            for (int i = 0; i < 256; i++)
            {
                lookupTable[i] = (byte)((cdf[i] * 255.0) / totalPixels);
            }

            // 应用查找表
            var result = new byte[pixels.Length];
            for (int i = 0; i < pixels.Length; i += 4)
            {
                result[i] = lookupTable[pixels[i]];
                result[i + 1] = lookupTable[pixels[i + 1]];
                result[i + 2] = lookupTable[pixels[i + 2]];
                result[i + 3] = pixels[i + 3];
            }

            return result;
        }

        /// <summary>
        /// 均值和标准差归一化
        /// </summary>
        private byte[] MeanStdDevNormalization(byte[] pixels, int width, int height)
        {
            // 计算输入图像的均值和标准差
            double sum = 0, sumSq = 0;
            int count = pixels.Length / 4;

            for (int i = 0; i < pixels.Length; i += 4)
            {
                double gray = (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3.0;
                sum += gray;
                sumSq += gray * gray;
            }

            double mean = sum / count;
            double stdDev = Math.Sqrt(sumSq / count - mean * mean);

            // 获取训练模式的均值和标准差
            var patternPixels = new byte[width * 4 * height];
            _trainedPattern.CopyPixels(patternPixels, width * 4, 0);

            double patternSum = 0, patternSumSq = 0;
            for (int i = 0; i < patternPixels.Length; i += 4)
            {
                double gray = (patternPixels[i] + patternPixels[i + 1] + patternPixels[i + 2]) / 3.0;
                patternSum += gray;
                patternSumSq += gray * gray;
            }

            double patternMean = patternSum / count;
            double patternStdDev = Math.Sqrt(patternSumSq / count - patternMean * patternMean);

            // 归一化
            var result = new byte[pixels.Length];
            for (int i = 0; i < pixels.Length; i += 4)
            {
                for (int c = 0; c < 3; c++)
                {
                    double normalized = (pixels[i + c] - mean) / stdDev * patternStdDev + patternMean;
                    result[i + c] = (byte)Math.Clamp(normalized, 0, 255);
                }
                result[i + 3] = pixels[i + 3];
            }

            return result;
        }

        /// <summary>
        /// 匹配尾部归一化
        /// </summary>
        private byte[] MatchTailsNormalization(byte[] pixels, int width, int height)
        {
            // 简化的匹配尾部实现
            return HistogramEqualization(pixels, width, height);
        }

        /// <summary>
        /// 局部校正归一化
        /// </summary>
        private byte[] LocalCorrectionNormalization(byte[] pixels, int width, int height)
        {
            int neighborhoodWidth = int.TryParse(NeighborhoodWidthText.Text, out int nw) ? nw : 32;
            int neighborhoodHeight = int.TryParse(NeighborhoodHeightText.Text, out int nh) ? nh : 32;

            var result = new byte[pixels.Length];

            for (int y = 0; y < height; y += neighborhoodHeight)
            {
                for (int x = 0; x < width; x += neighborhoodWidth)
                {
                    int blockW = Math.Min(neighborhoodWidth, width - x);
                    int blockH = Math.Min(neighborhoodHeight, height - y);

                    // 计算局部均值
                    double localSum = 0;
                    int localCount = blockW * blockH;

                    for (int by = 0; by < blockH; by++)
                    {
                        for (int bx = 0; bx < blockW; bx++)
                        {
                            int idx = ((y + by) * width + (x + bx)) * 4;
                            localSum += (pixels[idx] + pixels[idx + 1] + pixels[idx + 2]) / 3.0;
                        }
                    }

                    double localMean = localSum / localCount;
                    double adjustment = 128 - localMean;

                    // 应用调整
                    for (int by = 0; by < blockH; by++)
                    {
                        for (int bx = 0; bx < blockW; bx++)
                        {
                            int idx = ((y + by) * width + (x + bx)) * 4;
                            for (int c = 0; c < 3; c++)
                            {
                                result[idx + c] = (byte)Math.Clamp(pixels[idx + c] + adjustment, 0, 255);
                            }
                            result[idx + 3] = pixels[idx + 3];
                        }
                    }
                }
            }

            return result;
        }

        #endregion

        #region 辅助方法

        /// <summary>
        /// 应用缩放
        /// </summary>
        private void ApplyZoom()
        {
            var transform = new TransformGroup();
            transform.Children.Add(new ScaleTransform(_zoomScale, _zoomScale));
            transform.Children.Add(new TranslateTransform(_panOffset.X, _panOffset.Y));

            TrainParamImage.RenderTransform = transform;
            RegionImage.RenderTransform = transform;
            GraphicsImage.RenderTransform = transform;
        }

        /// <summary>
        /// 更新状态文本
        /// </summary>
        private void UpdateStatus(string text, SolidColorBrush color)
        {
            StatusText.Text = text;
            StatusText.Foreground = color;
        }

        /// <summary>
        /// 更新训练计数
        /// </summary>
        private void UpdateTrainingCount(int count)
        {
            TrainCountText.Text = $"训练图像数: {count}";
            TrainedRegionText.Text = $"已经过统计训练的区域: {count}";
        }

        /// <summary>
        /// 更新区域UI
        /// </summary>
        private void UpdateRegionUI()
        {
            OriginXText.Text = _regionOriginX.ToString("F2");
            OriginYText.Text = _regionOriginY.ToString("F2");
            SideXText.Text = _regionWidth.ToString("F2");
            SideYText.Text = _regionHeight.ToString("F2");
            RotationText.Text = _regionRotation.ToString("F2");
            SkewText.Text = _regionSkew.ToString("F2");
        }

        /// <summary>
        /// 更新图形叠加层
        /// </summary>
        private void UpdateGraphicsOverlay()
        {
            if (_currentInputImage == null) return;

            var canvas = GraphicsOverlay;
            canvas.Children.Clear();

            // 显示训练区域
            if (ShowRegionCheck.IsChecked == true)
            {
                var rect = new Rectangle
                {
                    Stroke = Brushes.Yellow,
                    StrokeThickness = 2,
                    Width = _regionWidth,
                    Height = _regionHeight
                };
                Canvas.SetLeft(rect, _regionOriginX - _regionWidth / 2);
                Canvas.SetTop(rect, _regionOriginY - _regionHeight / 2);
                canvas.Children.Add(rect);
            }

            // 显示原点
            if (ShowOriginCheck.IsChecked == true)
            {
                var crossSize = 10;
                var hLine = new Line
                {
                    Stroke = Brushes.Red,
                    StrokeThickness = 2,
                    X1 = _regionOriginX - crossSize,
                    Y1 = _regionOriginY,
                    X2 = _regionOriginX + crossSize,
                    Y2 = _regionOriginY
                };
                var vLine = new Line
                {
                    Stroke = Brushes.Red,
                    StrokeThickness = 2,
                    X1 = _regionOriginX,
                    Y1 = _regionOriginY - crossSize,
                    X2 = _regionOriginX,
                    Y2 = _regionOriginY + crossSize
                };
                canvas.Children.Add(hLine);
                canvas.Children.Add(vLine);
            }
        }

        /// <summary>
        /// 保存位图到文件
        /// </summary>
        private void SaveBitmap(BitmapSource bitmap, string filePath)
        {
            var encoder = filePath.EndsWith(".jpg") || filePath.EndsWith(".jpeg")
                ? (BitmapEncoder)new JpegBitmapEncoder()
                : filePath.EndsWith(".bmp")
                    ? (BitmapEncoder)new BmpBitmapEncoder()
                    : (BitmapEncoder)new PngBitmapEncoder();

            encoder.Frames.Add(BitmapFrame.Create(bitmap));

            using (var stream = new FileStream(filePath, FileMode.Create))
            {
                encoder.Save(stream);
            }
        }

        /// <summary>
        /// 保存训练模式
        /// </summary>
        private void SavePattern(string filePath)
        {
            // 简化的模式保存，实际应该保存更多参数
            if (_trainedPattern != null)
            {
                SaveBitmap(_trainedPattern, filePath + ".png");
            }
        }

        /// <summary>
        /// 加载训练模式
        /// </summary>
        private void LoadPattern(string filePath)
        {
            var bitmap = new BitmapImage(new Uri(filePath + ".png"));
            _trainedPattern = ConvertToWriteableBitmap(bitmap);
            PatternImage.Source = _trainedPattern;
            _isTrained = true;
            UpdateStatus("已加载", DarkGreenBrush);
        }

        /// <summary>
        /// 处理单张图像（批量处理用）
        /// </summary>
        private BatchResult ProcessSingleImage(string filePath, int maxDefectPixels, double maxDefectRatio)
        {
            var startTime = DateTime.Now;
            
            // 加载图像
            var bitmap = new BitmapImage(new Uri(filePath));
            var image = ConvertToWriteableBitmap(bitmap);
            
            // 临时设置当前图像
            var originalImage = _currentInputImage;
            _currentInputImage = image;
            
            // 执行检测
            PerformInspection();
            
            // 获取结果
            int defectCount = int.Parse(DefectPixelCountText.Text);
            double defectRatio = double.Parse(DefectRatioText.Text.Replace("%", ""));
            
            // 保存结果图像
            if (SaveDiffImageCheck.IsChecked == true && ThresholdDiffImage.Source != null)
            {
                string diffPath = System.IO.Path.Combine(BatchOutputFolderText.Text, 
                    System.IO.Path.GetFileNameWithoutExtension(filePath) + "_diff.png");
                SaveBitmap((BitmapSource)ThresholdDiffImage.Source, diffPath);
            }
            
            // 恢复原始图像
            _currentInputImage = originalImage;
            
            var elapsed = (DateTime.Now - startTime).TotalMilliseconds;
            
            return new BatchResult
            {
                FileName = System.IO.Path.GetFileName(filePath),
                DefectPixelCount = defectCount,
                DefectRatio = $"{defectRatio:F2}%",
                Result = defectCount <= maxDefectPixels && defectRatio <= maxDefectRatio ? "通过" : "失败",
                ProcessTime = $"{elapsed:F0}ms"
            };
        }

        /// <summary>
        /// 保存批量处理报告
        /// </summary>
        private void SaveBatchReport(List<BatchResult> results)
        {
            string reportPath = System.IO.Path.Combine(BatchOutputFolderText.Text, "report.csv");
            var sb = new StringBuilder();
            sb.AppendLine("文件名,缺陷像素数,缺陷比例,结果,处理时间");
            
            foreach (var result in results)
            {
                sb.AppendLine($"{result.FileName},{result.DefectPixelCount},{result.DefectRatio},{result.Result},{result.ProcessTime}");
            }
            
            File.WriteAllText(reportPath, sb.ToString());
        }

        #endregion
    }

    /// <summary>
    /// 批量处理结果类
    /// </summary>
    public class BatchResult
    {
        public string FileName { get; set; }
        public int DefectPixelCount { get; set; }
        public string DefectRatio { get; set; }
        public string Result { get; set; }
        public string ProcessTime { get; set; }
    }
}
