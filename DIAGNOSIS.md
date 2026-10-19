# MarkItDown 体验诊断（实测记录）

> 对象：`microsoft/markitdown` 0.1.8（PyPI 最新，2026-10-19 实测）
> 环境：Windows 11 / Python 3.13.14 / 干净 venv
> 方法：全部结论来自真实命令执行，非文档推断

## 一、热度与维护状态

| 项 | 值 |
|---|---|
| Stars | 191,051 |
| 语言 | Python |
| Open issues | 738 |
| 最近推送 | 2026-10-04 |

功能真实可用：`[all]` 安装后 PDF / DOCX / 中文 / emoji 均正确输出。

## 二、五类实际障碍（逐条实测）

### 障碍 1：默认安装等于「装了个不能用」的壳

```
pip install markitdown          # 退出码 0，用户以为成功
markitdown file.pdf             # 退出码 1
```

输出是 30 行 Python traceback，末行才是真正原因：

```
markitdown._exceptions.FileConversionException: File conversion failed after 1 attempts:
 - PdfConverter threw MissingDependencyException with message: PdfConverter recognized
   the input as a potential .pdf file, but the dependencies needed to read .pdf files
   have not been installed...
```

DOCX 完全同样。默认安装只能转 txt/csv/json/html/xml/ipynb/zip/epub —— 恰好不含普通人最常用的 PDF 和 Word。

**根因**：`pyproject.toml` 的 `dependencies` 只有 6 个通用包，所有文档格式都挂在 `optional-dependencies` 下。而 README 的 Installation 段直接写 `pip install 'markitdown[all]'`，很多人按直觉 `pip install markitdown` 就装错了。

### 障碍 2：装之前无法知道「我这些文件能不能转」

没有 `doctor` / `--check` / `--dry-run`。只能逐个文件试，试错成本 = 一次 traceback。上游 issue #2361（closed）标题就是 `Default install cannot convert DOCX`，说明这不是个例。

### 障碍 3：静默成功 + 空输出（最危险）

| 输入 | 退出码 | 输出字节 |
|---|---|---|
| `photo.png`（无 exiftool、无 OCR） | **0** | **2**（一个换行） |
| `scanned.pdf`（无文本层扫描件） | **0** | **2** |

退出码 0、无任何警告、stdout 只有空行。批处理管道会安静地生成一堆空 `.md`，下游 LLM 拿到空上下文却不报错。

### 障碍 4：错误提示是 Python 内部实现，不是用户语言

| 用户操作 | 实际报错 |
|---|---|
| 传目录 `markitdown ./docs` | `PermissionError: [Errno 13] Permission denied: 'fixtures/batch'` |
| 传不存在的文件 | `FileNotFoundError: [Errno 2] No such file or directory: 'nope.pdf'`（20 行 traceback） |
| 传未知格式 | `UnsupportedFormatException: ...suggesting that the filetype is simply not supported.` |

`PermissionError` 对目录输入属于误导性提示 —— 用户会去查权限，真正原因是「本工具不支持目录」。

### 障碍 5：无批量能力

上游 3 个重复的 open feature request：#1371、#1372、#1825、#2353 全在要「目录批量转换」。现状只能 shell 循环，且循环里任何空输出都不可见（叠加障碍 3）。

## 三、选定改造环节

**「安装 → 首次成功转换」这段真空地带**（障碍 1/2/3/4 共同作用的结果）。

理由：
- 它挡在 100% 新用户面前，而障碍 5（批量）只挡进阶用户
- 它可由外部包实现，**完全不动 markitdown 核心代码**，保留全部原有能力
- 障碍 3 的静默空输出是唯一会造成「下游数据污染」的问题，优先级最高

不选「改 PDF 解析质量」（#293/#1845/#2215 那类）—— 那要重写解析器，且属于核心能力，违背「保留原核心能力」。
