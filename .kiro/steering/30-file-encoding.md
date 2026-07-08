# 文件编码约定（防中文乱码）

## 规则

1. **所有文本文件统一使用 UTF-8 编码**（尤其含中文的文档：spec、steering、markdown、代码注释等）。
2. **禁止用 PowerShell `Set-Content` / `Out-File` 改写含中文（或任何非 ASCII）的既有文件**。Windows PowerShell 5.1 的 `Set-Content`/`Out-File` 默认编码不是 UTF-8（常为 ANSI/GBK 或 UTF-16），会把已有 UTF-8 中文重新编码写回，导致乱码损坏。
3. **修改文件一律使用编辑工具**（`str_replace` / `fsWrite` / `fsAppend`），它们以 UTF-8 写入，不破坏编码。批量勾选复选框、批量替换等，也用 `str_replace` 多次调用，不要用 shell 重写整文件。
4. 若确需用命令行处理文本，**必须显式指定 UTF-8**（例如 PowerShell 用 `Set-Content -Encoding utf8`，或改用 `python` 以 `encoding="utf-8"` 读写），且优先仅用于读取/搜索而非重写。
5. 读取 docx 等二进制文档时，提取出的临时文本文件用完即删，避免污染工作区。

## 背景（一次真实教训）

曾用 `powershell (Get-Content -Raw) ... | Set-Content` 批量勾选 tasks.md 复选框，导致整份中文文档变乱码。根因即 `Set-Content` 的默认非 UTF-8 编码。此后一律遵守本约定。
