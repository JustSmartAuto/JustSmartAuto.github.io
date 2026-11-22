// extract_xml_to_files.go
//
// 解包 bundle_cs_to_xml.go 生成的 XML 源码包，按相对路径还原所有文件。
//
// 用法：
//   1) 双击运行，按提示把 XML 文件拖进窗口后回车；
//   2) 命令行：extract_xml_to_files.exe -xml <包文件> [-out <输出目录>] [-force]
//
// 默认输出到 <XML所在目录>/<项目名>_extracted_yyyyMMdd_HHmmss，带时间戳避免覆盖旧目录。
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
)

type fileEntry struct {
	Path     string `xml:"path,attr"`
	Encoding string `xml:"encoding,attr"`
	Size     int64  `xml:"size,attr"`
	SHA256   string `xml:"sha256,attr"`
	Content  string `xml:",chardata"`
}

func main() {
	enableUTF8Console()

	var (
		xmlFlag = flag.String("xml", "", "XML 源码包路径")
		outFlag = flag.String("out", "", "输出目录")
		force   = flag.Bool("force", false, "覆盖内容不同的已有文件")
	)
	flag.Parse()

	interactive := *xmlFlag == "" && flag.NArg() == 0
	xmlPath := *xmlFlag
	if xmlPath == "" && flag.NArg() > 0 {
		xmlPath = flag.Arg(0)
	}

	reader := bufio.NewReader(os.Stdin)
	if interactive {
		fmt.Println("============================================================")
		fmt.Println("  XML 源码包解包工具（配套 bundle_cs_to_xml）")
		fmt.Println("============================================================")
		xmlPath = prompt(reader, "请拖入 XML 源码包（或输入路径）后回车", "")
	}
	xmlPath = cleanInputPath(xmlPath)
	if xmlPath == "" {
		fatal(interactive, reader, "未提供 XML 路径")
	}
	fi, err := os.Stat(xmlPath)
	if err != nil || fi.IsDir() {
		fatal(interactive, reader, "XML 文件不存在："+xmlPath)
	}
	absXML, err := filepath.Abs(xmlPath)
	if err != nil {
		fatal(interactive, reader, "解析 XML 路径失败："+err.Error())
	}

	// 先读根节点，拿到项目名用于默认输出目录
	bundleName, err := readBundleName(absXML)
	if err != nil {
		fatal(interactive, reader, "读取 XML 失败："+err.Error())
	}

	stamp := time.Now().Format("20060102_150405")
	out := cleanInputPath(*outFlag)
	if out == "" {
		def := filepath.Join(filepath.Dir(absXML), fmt.Sprintf("%s_extracted_%s", bundleName, stamp))
		if interactive {
			out = cleanInputPath(prompt(reader, "输出目录（回车使用默认）", def))
		} else {
			out = def
		}
	}
	absOut, err := filepath.Abs(out)
	if err != nil {
		fatal(interactive, reader, "解析输出目录失败："+err.Error())
	}

	fmt.Printf("源码包：%s\n输出目录：%s\n\n开始解包...\n", absXML, absOut)

	res, err := extract(absXML, absOut, *force)
	if err != nil {
		fatal(interactive, reader, "解包失败："+err.Error())
	}

	fmt.Println("\n==================== 解包完成 ====================")
	fmt.Printf("还原文件：%d 个（%s）\n", res.Written, humanBytes(res.Bytes))
	if res.Conflicts > 0 {
		fmt.Printf("因内容不同被跳过的已有文件：%d 个（加 -force 可覆盖）\n", res.Conflicts)
	}
	if res.HashMismatch > 0 {
		fmt.Printf("警告：%d 个文件 SHA256 与包内记录不一致（内容可能已被修改）\n", res.HashMismatch)
		for _, p := range res.BadHash {
			fmt.Printf("  [校验失败] %s\n", p)
		}
	}
	fmt.Printf("输出位置：%s\n", absOut)

	if interactive {
		fmt.Print("\n按回车键退出...")
		_, _ = reader.ReadString('\n')
	}
}

type extractResult struct {
	Written      int
	Bytes        int64
	Conflicts    int
	HashMismatch int
	BadHash      []string
}

func readBundleName(xmlPath string) (string, error) {
	f, err := os.Open(xmlPath)
	if err != nil {
		return "", err
	}
	defer f.Close()

	dec := xml.NewDecoder(bufio.NewReaderSize(f, 1<<20))
	for {
		tok, err := dec.Token()
		if err != nil {
			if err == io.EOF {
				break
			}
			return "", err
		}
		if se, ok := tok.(xml.StartElement); ok {
			if se.Name.Local != "bundle" {
				return "", fmt.Errorf("不是有效的源码包（根节点为 %s）", se.Name.Local)
			}
			for _, a := range se.Attr {
				if a.Name.Local == "name" && a.Value != "" {
					return sanitizeName(a.Value), nil
				}
			}
			return "project", nil
		}
	}
	return "", fmt.Errorf("XML 内容为空")
}

func extract(xmlPath, outDir string, force bool) (*extractResult, error) {
	f, err := os.Open(xmlPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return nil, err
	}

	res := &extractResult{}
	dec := xml.NewDecoder(bufio.NewReaderSize(f, 1<<20))
	for {
		tok, err := dec.Token()
		if err != nil {
			if err == io.EOF {
				break
			}
			return nil, err
		}
		se, ok := tok.(xml.StartElement)
		if !ok || se.Name.Local != "file" {
			continue
		}
		var entry fileEntry
		if err := dec.DecodeElement(&entry, &se); err != nil {
			return nil, fmt.Errorf("解析文件节点失败：%w", err)
		}

		rel, err := safeRelPath(entry.Path)
		if err != nil {
			return nil, err
		}
		data, err := restoreBytes(entry)
		if err != nil {
			return nil, fmt.Errorf("还原 %s 失败：%w", entry.Path, err)
		}

		if entry.SHA256 != "" {
			sum := sha256.Sum256(data)
			got := hex.EncodeToString(sum[:])
			if !strings.EqualFold(got, entry.SHA256) {
				res.HashMismatch++
				res.BadHash = append(res.BadHash, entry.Path)
			}
		}

		dst := filepath.Join(outDir, rel)
		if !isWithin(outDir, dst) {
			return nil, fmt.Errorf("文件路径越界：%s", entry.Path)
		}

		if old, err := os.ReadFile(dst); err == nil {
			if bytes.Equal(old, data) {
				continue // 内容一致，无需重写
			}
			if !force {
				res.Conflicts++
				fmt.Printf("  [跳过] 已存在且内容不同：%s\n", dst)
				continue
			}
		}

		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(dst, data, 0o644); err != nil {
			return nil, err
		}
		res.Written++
		res.Bytes += int64(len(data))
		if res.Written%200 == 0 {
			fmt.Printf("  已还原 %d 个文件...\n", res.Written)
		}
	}
	return res, nil
}

// restoreBytes 按 encoding 把 XML 文本还原为原始字节
func restoreBytes(e fileEntry) ([]byte, error) {
	switch e.Encoding {
	case "", "utf-8":
		return []byte(e.Content), nil
	case "utf-8-bom":
		return append([]byte{0xEF, 0xBB, 0xBF}, []byte(e.Content)...), nil
	case "utf-16le":
		return encodeUTF16(e.Content, true), nil
	case "utf-16be":
		return encodeUTF16(e.Content, false), nil
	case "base64":
		return base64.StdEncoding.DecodeString(strings.TrimSpace(e.Content))
	default:
		return nil, fmt.Errorf("未知编码标记 %q", e.Encoding)
	}
}

func encodeUTF16(s string, littleEndian bool) []byte {
	units := utf16.Encode([]rune(s))
	out := make([]byte, 0, len(units)*2+2)
	if littleEndian {
		out = append(out, 0xFF, 0xFE)
		for _, u := range units {
			out = append(out, byte(u), byte(u>>8))
		}
	} else {
		out = append(out, 0xFE, 0xFF)
		for _, u := range units {
			out = append(out, byte(u>>8), byte(u))
		}
	}
	return out
}

// safeRelPath 防止绝对路径、盘符和 .. 穿越
func safeRelPath(p string) (string, error) {
	if p == "" {
		return "", fmt.Errorf("包内存在空文件路径")
	}
	p = strings.ReplaceAll(p, "\\", "/")
	if strings.HasPrefix(p, "/") || strings.Contains(p, ":") {
		return "", fmt.Errorf("非法文件路径（绝对路径或盘符）：%s", p)
	}
	clean := filepath.Clean(filepath.FromSlash(p))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("非法文件路径（越界）：%s", p)
	}
	return clean, nil
}

func isWithin(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

func sanitizeName(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "project"
	}
	var b strings.Builder
	for _, r := range s {
		switch r {
		case '<', '>', ':', '"', '/', '\\', '|', '?', '*':
			b.WriteRune('_')
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// ---------------- 小工具 ----------------

func cleanInputPath(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "&")
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
