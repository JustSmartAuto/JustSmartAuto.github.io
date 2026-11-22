// bundle_cs_to_xml.go
//
// 交互式 CLI：把 C#（或任意）项目里所有可读的文本文件（源码 + 配置）打包成一个 XML。
// 参考 随车资料自动核对_bundle_2605070954.md 的“单文件源码包”思路，改为 XML 载体，
// 并保留字节级保真（UTF-8 / UTF-16 直接存文本，GBK 等非 UTF-8 文本按 base64 原样保存）。
//
// 用法：
//   1) 双击运行，按提示把项目文件夹拖进窗口后回车；
//   2) 命令行：bundle_cs_to_xml.exe -src <项目目录> [-out <输出xml>] [-all] [-max-mb 64]
//
// 输出文件名默认 <项目名>_bundle_yyyyMMdd_HHmmss.xml，带时间戳便于区分。
package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/xml"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unicode/utf8"
)

// 默认跳过的目录（可用 -all 关闭）
var defaultIgnoredDirs = map[string]bool{
	".git": true, ".svn": true, ".hg": true, ".vs": true,
	".idea": true, ".vscode": true,
	"bin": true, "obj": true, "node_modules": true,
	"packages": true, "TestResults": true,
}

func main() {
	enableUTF8Console()

	var (
		srcFlag = flag.String("src", "", "项目目录（也可直接拖文件夹到 exe 上）")
		outFlag = flag.String("out", "", "输出 XML 文件路径")
		allFlag = flag.Bool("all", false, "包含 bin/obj/.git 等默认忽略目录")
		maxMB   = flag.Int("max-mb", 64, "单个文件允许打包的最大体积（MB），超出跳过")
	)
	flag.Parse()

	interactive := *srcFlag == "" && flag.NArg() == 0
	src := *srcFlag
	if src == "" && flag.NArg() > 0 {
		src = flag.Arg(0)
	}

	reader := bufio.NewReader(os.Stdin)
	if interactive {
		fmt.Println("============================================================")
		fmt.Println("  C# 项目源码打包为 XML（可读文本文件全量打包）")
		fmt.Println("  默认跳过 .git/.vs/bin/obj/packages 等目录，-all 可关闭")
		fmt.Println("============================================================")
		src = prompt(reader, "请拖入项目文件夹（或输入项目路径）后回车", "")
	}
	src = cleanInputPath(src)
	if src == "" {
		fatal(interactive, reader, "未提供项目路径")
	}
	info, err := os.Stat(src)
	if err != nil || !info.IsDir() {
		fatal(interactive, reader, "项目目录不存在或不是文件夹："+src)
	}
	absSrc, err := filepath.Abs(src)
	if err != nil {
		fatal(interactive, reader, "解析项目路径失败："+err.Error())
	}

	cwd, _ := os.Getwd()
	stamp := time.Now().Format("20060102_150405")
	out := cleanInputPath(*outFlag)
	if out == "" {
		def := filepath.Join(cwd, fmt.Sprintf("%s_bundle_%s.xml", filepath.Base(absSrc), stamp))
		if interactive {
			out = cleanInputPath(prompt(reader, "输出 XML 文件路径（回车使用默认）", def))
		} else {
			out = def
		}
	}
	if filepath.Ext(out) == "" {
		out += ".xml"
	}
	absOut, err := filepath.Abs(out)
	if err != nil {
		fatal(interactive, reader, "解析输出路径失败："+err.Error())
	}
	if err := os.MkdirAll(filepath.Dir(absOut), 0o755); err != nil {
		fatal(interactive, reader, "创建输出目录失败："+err.Error())
	}

	fmt.Printf("项目目录：%s\n输出文件：%s\n\n开始扫描并打包...\n", absSrc, absOut)

	result, err := bundle(absSrc, absOut, *allFlag, int64(*maxMB)*1024*1024)
	if err != nil {
		fatal(interactive, reader, "打包失败："+err.Error())
	}

	fmt.Println("\n==================== 打包完成 ====================")
	fmt.Printf("已打包文本文件：%d 个（%s）\n", result.Packed, humanBytes(result.PackedBytes))
	fmt.Printf("跳过二进制/超大文件：%d 个\n", result.Skipped)
	for _, s := range result.SkipList {
		fmt.Printf("  [跳过] %s（%s）\n", s.Path, s.Reason)
	}
	fmt.Printf("XML 大小：%s\n输出位置：%s\n", humanBytes(result.XMLSize()), absOut)

	if interactive {
		fmt.Print("\n按回车键退出...")
		_, _ = reader.ReadString('\n')
	}
}

// ---------------- 打包核心 ----------------

type skipEntry struct {
	Path   string
	Reason string
}

type bundleResult struct {
	Packed      int
	PackedBytes int64
	Skipped     int
	SkipList    []skipEntry
	outPath     string
}

func (r *bundleResult) XMLSize() int64 {
	if fi, err := os.Stat(r.outPath); err == nil {
		return fi.Size()
	}
	return 0
}

func bundle(srcDir, outPath string, includeAll bool, maxFileBytes int64) (*bundleResult, error) {
	f, err := os.Create(outPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	w := bufio.NewWriterSize(f, 1<<20)

	res := &bundleResult{outPath: outPath}

	writeStr := func(s string) error {
		_, err := io.WriteString(w, s)
		return err
	}

	if err := writeStr("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"); err != nil {
		return nil, err
	}
	if err := writeStr("<bundle version=\"1\" tool=\"bundle_cs_to_xml\" name=" + xmlAttr(filepath.Base(srcDir)) +
		" source=" + xmlAttr(srcDir) +
		" created=" + xmlAttr(time.Now().Format(time.RFC3339)) + ">\n"); err != nil {
		return nil, err
	}

	absOut := outPath
	walkErr := filepath.WalkDir(srcDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			fmt.Printf("  [警告] 无法访问 %s：%v\n", path, err)
			return nil // 单个文件失败不中断整体打包
		}
		if d.IsDir() {
			if path != srcDir && !includeAll && defaultIgnoredDirs[d.Name()] {
				return filepath.SkipDir
			}
			return nil
		}
		if !d.Type().IsRegular() {
			return nil
		}
		if samePath(path, absOut) {
			return nil // 避免把输出 XML 自己打包进去
		}

		rel, err := filepath.Rel(srcDir, path)
		if err != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)

		fi, err := d.Info()
		if err != nil {
			return nil
		}
		if fi.Size() > maxFileBytes {
			res.Skipped++
			res.SkipList = append(res.SkipList, skipEntry{rel, "超过大小限制"})
			return nil
		}

		data, err := os.ReadFile(path)
		if err != nil {
			res.Skipped++
			res.SkipList = append(res.SkipList, skipEntry{rel, "读取失败：" + err.Error()})
			return nil
		}

		enc, text, ok := classifyText(data)
		if !ok {
			res.Skipped++
			res.SkipList = append(res.SkipList, skipEntry{rel, "二进制文件"})
			return nil
		}

		sum := sha256.Sum256(data)
		if err := writeStr("\t<file path=" + xmlAttr(rel) +
			" encoding=" + xmlAttr(enc) +
			fmt.Sprintf(" size=\"%d\"", len(data)) +
			" sha256=" + xmlAttr(hex.EncodeToString(sum[:])) + ">"); err != nil {
			return err
		}

		if enc == "base64" {
			if err := writeStr(base64.StdEncoding.EncodeToString(data)); err != nil {
				return err
			}
		} else {
			if err := xml.EscapeText(w, []byte(sanitizeXML(text))); err != nil {
				return err
			}
		}
		if err := writeStr("</file>\n"); err != nil {
			return err
		}

		res.Packed++
		res.PackedBytes += int64(len(data))
		if res.Packed%200 == 0 {
			fmt.Printf("  已打包 %d 个文件...\n", res.Packed)
		}
		return nil
	})
	if walkErr != nil {
		return nil, walkErr
	}

	if err := writeStr(fmt.Sprintf("\t<summary files=\"%d\" skipped=\"%d\" bytes=\"%d\"/>\n</bundle>\n",
		res.Packed, res.Skipped, res.PackedBytes)); err != nil {
		return nil, err
	}
	if err := w.Flush(); err != nil {
		return nil, err
	}
	return res, nil
}

// classifyText 判定文件内容如何入包。
// 返回 encoding（utf-8 / utf-8-bom / utf-16le / utf-16be / base64）、可写文本、是否可读文本。
func classifyText(data []byte) (enc string, text string, ok bool) {
	if len(data) == 0 {
		return "utf-8", "", true
	}
	// 含 XML 1.0 非法控制字符时不能按文本入包：写入端会替换成 U+FFFD，
	// 还原后字节不一致；改按 base64 原样保存。
	if looksText(data) && hasForbiddenControl(data) {
		return "base64", "", true
	}
	// UTF-8 BOM
	if bytes.HasPrefix(data, []byte{0xEF, 0xBB, 0xBF}) {
		body := data[3:]
		if utf8.Valid(body) && looksText(body) {
			return "utf-8-bom", string(body), true
		}
		if looksText(body) {
			return "base64", "", true
		}
		return "", "", false
	}
	// UTF-16（C# 工程里部分 .sln/.ps1/导出文件可能是 UTF-16）
	if len(data)%2 == 0 {
		if bytes.HasPrefix(data, []byte{0xFF, 0xFE}) {
			if s, ok2 := decodeUTF16(data[2:], true); ok2 {
				return "utf-16le", s, true
			}
		}
		if bytes.HasPrefix(data, []byte{0xFE, 0xFF}) {
			if s, ok2 := decodeUTF16(data[2:], false); ok2 {
				return "utf-16be", s, true
			}
		}
	}
	if utf8.Valid(data) && looksText(data) {
		return "utf-8", string(data), true
	}
	// GBK/GB2312/Big5 等非 UTF-8 文本：按 base64 原样保存，保证还原后字节一致
	if looksText(data) {
		return "base64", "", true
	}
	return "", "", false
}

func decodeUTF16(data []byte, littleEndian bool) (string, bool) {
	u16 := make([]uint16, 0, len(data)/2)
	for i := 0; i+1 < len(data); i += 2 {
		if littleEndian {
			u16 = append(u16, uint16(data[i])|uint16(data[i+1])<<8)
		} else {
			u16 = append(u16, uint16(data[i])<<8|uint16(data[i+1]))
		}
	}
	runes := utf16.Decode(u16)
	for _, r := range runes {
		// 0 或 XML 非法字符/未配对代理项说明不是正常 UTF-16 文本，
		// 按 UTF-16 写入会在还原时被替换成 U+FFFD，必须退回 base64。
		if r == 0 ||
			(r < 0x20 && r != '\t' && r != '\n' && r != '\r') ||
			r == 0xFFFE || r == 0xFFFF ||
			(r >= 0xD800 && r <= 0xDFFF) {
			return "", false
		}
	}
	return string(runes), true
}

// looksText 采样判断是否像文本：无 NUL，控制字符比例低
func looksText(b []byte) bool {
	sample := b
	if len(sample) > 8192 {
		sample = sample[:8192]
	}
	if len(sample) == 0 {
		return true
	}
	ctrl := 0
	for _, c := range sample {
		if c == 0 {
			return false
		}
		if c < 0x20 && c != '\t' && c != '\n' && c != '\r' && c != '\f' && c != 0x1b {
			ctrl++
		}
	}
	return ctrl*100 < len(sample)
}

// hasForbiddenControl 判断是否含有 XML 1.0 不允许的控制字符（\t \n \r 除外）。
func hasForbiddenControl(b []byte) bool {
	for _, c := range b {
		if c < 0x20 && c != '\t' && c != '\n' && c != '\r' {
			return true
		}
	}
	return false
}

// sanitizeXML 去掉 XML 1.0 不允许出现的字符，避免解码端报错
func sanitizeXML(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		switch {
		case r == '\t' || r == '\n' || r == '\r':
			b.WriteRune(r)
		case r < 0x20 || r == 0xFFFE || r == 0xFFFF || (r >= 0xD800 && r <= 0xDFFF):
			b.WriteRune('\uFFFD')
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// ---------------- 小工具 ----------------

func xmlAttr(s string) string {
	var buf bytes.Buffer
	_ = xml.EscapeText(&buf, []byte(s))
	return "\"" + buf.String() + "\""
}

func cleanInputPath(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "&") // 兼容 PowerShell 拖入带 & 的形式
	s = strings.TrimSpace(s)
	if len(s) >= 2 {
		if (s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'') {
			s = s[1 : len(s)-1]
		}
	}
	return strings.TrimSpace(s)
}

func prompt(reader *bufio.Reader, label, def string) string {
	if def != "" {
		fmt.Printf("%s\n  默认：%s\n> ", label, def)
	} else {
		fmt.Printf("%s\n> ", label)
	}
	line, _ := reader.ReadString('\n')
	line = cleanInputPath(line)
	if line == "" {
		return def
	}
	return line
}

func samePath(a, b string) bool {
	aa, err1 := filepath.Abs(a)
	bb, err2 := filepath.Abs(b)
	if err1 != nil || err2 != nil {
		return false
	}
	if runtime.GOOS == "windows" {
		return strings.EqualFold(aa, bb)
	}
	return aa == bb
}

func humanBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.2f %cB", float64(n)/float64(div), "KMGTPE"[exp])
}

func fatal(interactive bool, reader *bufio.Reader, msg string) {
	fmt.Println("错误：" + msg)
	if interactive {
		fmt.Print("按回车键退出...")
		_, _ = reader.ReadString('\n')
	}
	os.Exit(1)
}

// enableUTF8Console 让中文提示在 Windows 控制台正常显示
func enableUTF8Console() {
	if runtime.GOOS != "windows" {
		return
	}
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	if proc := kernel32.NewProc("SetConsoleOutputCP"); proc != nil {
		_, _, _ = proc.Call(65001)
	}
	if proc := kernel32.NewProc("SetConsoleCP"); proc != nil {
		_, _, _ = proc.Call(65001)
	}
}
