# 验收记录

> 目标：验证「一个没见过这个项目的新用户，只读 README，能否完成安装到实际转换」。
> 环境：Windows 11 / Python 3.13.14 / 全新 venv（每次验收都重建）
> 时间：2026-10-19

## 一、改造前的基线（原始 markitdown 0.1.8）

| 输入 | 退出码 | stdout | 用户能否判断 |
|---|---|---|---|
| `sample.pdf`（装了 `[all]`） | 0 | 正确 Markdown | 能 |
| `scanned.pdf`（无文本层） | **0** | **空**（1 个换行） | **不能** |
| `photo.png`（无 OCR） | **0** | **空**（1 个换行） | **不能** |
| `broken.bin`（损坏二进制） | **0** | **乱码原文** | **不能** |
| 目录 `fixtures/batch` | 1 | 30 行 traceback，`PermissionError [Errno 13]` | 会被误导去查权限 |

结论：4 个文件里 3 个「静默错误」——退出码 0 但结果无用。这是本次改造的核心目标。

## 二、改造后的同一组输入

| 输入 | 退出码 | 判定 | 输出是否写入磁盘 |
|---|---|---|---|
| `sample.pdf` | 0 | `ok` | 是 |
| `scanned.pdf` | 1 | `empty` + OCR 建议 | 否 |
| `photo.png` | 1 | `NO TEXT` + exiftool/OCR 建议 | 否 |
| `broken.bin` | 1 | `junk`（识别为 mojibake） | 否 |
| 目录 | 0/1 | 自动进入批量模式 | — |

## 三、新用户全流程验收（从 wheel 安装）

环境：`_probe/venv-wheel`，全新 venv，只装 wheel。

| 步骤 | 命令 | 结果 | 通过 |
|---|---|---|---|
| 1 | `pip install markitdown_doctor-0.1.0-py3-none-any.whl` | 退出 0，`mdc --version` → `mdc 0.1.0` | ✅ |
| 2 | `mdc doctor` | 列出 7 个缺失 extra + 完整安装命令 | ✅ |
| 3 | `mdc check documents/` | 5 文件，2 ready / 3 blocked，给出 `markitdown[docx,pdf]` | ✅ |
| 4 | `pip install 'markitdown[docx,pdf]'` | 退出 0 | ✅ |
| 5 | `mdc doctor` 复查 | pdf/docx 转为 `[x]` | ✅ |
| 6 | `mdc convert documents/report.pdf` | 输出正确 Markdown，退出 0 | ✅ |
| 7 | `mdc convert ./documents -r -o ./markdown` | 3 成功 / 1 empty / 1 junk，只写 3 个 md | ✅ |

**步骤 3 是最关键的一环**：`check` 从实际文件扩展名算出 `markitdown[docx,pdf]`，而不是让用户装 `[all]`。这证明「按需最小安装」这条路是通的。

## 四、兼容性验收

| 项 | 方法 | 结果 |
|---|---|---|
| 与原 CLI 输出一致 | `cmp` 逐字节对比 PDF | IDENTICAL |
| 与原 CLI 输出一致 | `cmp` 逐字节对比中文 DOCX | IDENTICAL |
| 中文不丢字 | 输出 `季度报告：营收同比增长 12%…🚀` | 正确 |
| 编码 | 输出文件 | UTF-8 无 BOM |
| 无 markitdown 时 | 全新 venv 只装 doctor | 提示 `pip install markitdown-doctor`，退出 2 |
| 只装 pdf 时转 docx | `venv-partial` | 3 行说明 + `pip install 'markitdown[docx]'` |

## 五、乱码检测误伤测试

对 9 类文本样本跑 `_looks_binary()`：

| 样本 | 判定 |
|---|---|
| 中文、日文、阿拉伯文 | text |
| 代码、Markdown 表格 | text |
| emoji、混合标点、含 tab | text |
| 二进制乱码 | **BINARY** |

零误伤。

## 六、自动化验证

- `pytest`：**60 passed**
- `ruff check src tests`：**All checks passed**
- 覆盖：格式矩阵、预检六种状态、异常翻译、批量计划与冲突命名、CLI 退出码、真实子进程端到端

## 七、遗留与已知限制

1. 格式矩阵钉在 MarkItDown 0.1.8；上游新增转换器需同步 `formats.py`。
2. 乱码检测是启发式（控制字符密度），已用 9 类样本验证但非形式化证明。
3. `--json` 结构稳定但未做版本号。
4. 未在 macOS / Linux 实机跑过；代码只用 `pathlib` 与 `shutil.which`，理论可移植。
5. 未发布到 PyPI（名称 `markitdown-doctor` 在 PyPI 上 404，可用）。
