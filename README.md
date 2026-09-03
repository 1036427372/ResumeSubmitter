# 投了么

投了么是一个仅保留 Python 版本的本地简历投递助手。它把个人资料、结构化简历经历、附件、网页字段快照、AI 对话和投递轨迹放在一个可扩展的本地资料库中，帮助你在招聘官网手动登录后完成信息整理和半自动填写。

> 这是一个本地辅助工具，不会替你提交申请、绕过验证码、破解登录或自动上传敏感文件。提交、上传和最终确认始终由使用者完成。

## 功能概览

- 首次使用导入一份主简历，支持 PDF、DOCX、TXT；主简历始终只保留一份。
- 解析姓名、联系方式、教育、工作/实习、项目、技能、语言、证书、奖项、论文、培训、作品链接和个人评价等内容。
- 个人资料库支持多次补充；本科、硕士、多段工作和多段项目不会互相覆盖。
- 在内置浏览器中打开招聘官网，用户自行登录后扫描当前页面字段。
- 本地规则优先处理姓名、邮箱、手机号、城市、学历等常见字段；配置模型后，AI 负责上下文识别和字段归类。
- 能区分候选人姓名、导师姓名、亲属姓名、紧急联系人、证明人/推荐人等同名字段。
- 支持只填写空字段，避免覆盖官网中已经填写的内容。
- 网页字段审核表显示：网页是否已填写、资料库是否已有、字段归类、上下文、处理方式和判断原因。
- 支持快速选择：全选、清空、资料库已有、资料库缺少、网页已填写、网页未填写。
- 同一个网址的多次导出会合并到同一个 JSON 或 CSV 文件，并按字段身份去重、更新。
- 每个字段都可以单独确认“是否加入资料库”，并选择合并到基础资料、某类经历、自定义字段或公司待补要求。
- 投递记录支持自动记录当前页面、手动新增、状态、日期、失败原因、备注和 CSV 导出。
- AI 对话支持自由问答、润色自我介绍、总结问题、从对话提取资料并审核保存。

## 目录结构

源代码目录：

```text
ResumeSubmitter/
├─ python_app/
│  ├─ main.py                 # PySide6 主界面和业务流程
│  ├─ browser_fill.py         # 网页扫描、字段映射和填充脚本
│  ├─ llm.py                  # 模型请求、分类和对话
│  ├─ resume_parser.py        # PDF/DOCX/TXT 简历解析
│  ├─ storage.py              # 本地资料库和原子保存
│  ├─ profile_schema.py       # 字段、经历类型、状态注册表
│  ├─ ui_theme.py             # 界面样式
│  ├─ requirements.txt        # Python 依赖
│  ├─ build_windows.ps1       # Windows 打包脚本
│  └─ assets/                 # 投了么 Logo 和图标
├─ .gitignore
└─ README.md
```

用户资料默认不放在代码目录，而放在：

```text
%USERPROFILE%\Documents\简历投递器资料库\
├─ profile.json               # 个人资料、经历、AI 设置和待补问题
├─ applications.json          # 投递记录
├─ attachments/               # 主简历、证书、作品等附件
├─ company-records/            # 按网址合并的网页字段 JSON/CSV
├─ browser-profile/           # 内置浏览器登录 Cookie 和站点缓存
└─ browser-cache/
```

也可以在启动前指定其他位置，例如移动硬盘或加密目录：

```powershell
$env:RESUME_SUBMITTER_LIBRARY = "D:\求职资料库"
python .\python_app\main.py
```

不要把这个目录复制到 GitHub。仓库的 `.gitignore` 已默认忽略这些文件夹和文件，但提交前仍应人工检查。

## 环境要求

- Windows 10/11（内置浏览器依赖 Qt WebEngine）。
- Python 3.10 或更高版本，建议使用 64 位 Python。
- 如需 AI：一个兼容 OpenAI Chat Completions 格式的接口地址、模型名和 API Key。
- 如需打包：PyInstaller；它已包含在 `requirements.txt`。

## 安装与启动

在 PowerShell 中执行：

```powershell
cd C:\path\to\ResumeSubmitter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\python_app\requirements.txt
python .\python_app\main.py
```

如果 PowerShell 禁止激活脚本，可以不激活虚拟环境，直接调用解释器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\python_app\requirements.txt
.\.venv\Scripts\python.exe .\python_app\main.py
```

首次启动时应用会创建资料库目录，并生成稳定的 `profileId`。这个 ID 只用于未来同步和导出关联，不包含姓名、电话等个人内容。

## 首次使用流程

1. 打开“投了么”。
2. 点击“导入并解析我的简历”。
3. 选择 PDF、DOCX 或 TXT 简历。
4. 应用会先复制一份到 `attachments/`，再解析内容。
5. 在确认窗口勾选需要写入的项目；不确定的项目可以取消，之后再从 AI 对话或个人资料页面补充。
6. 打开“个人资料”和“经历、技能与附加内容”检查解析结果。
7. 需要润色自我介绍时进入“AI 对话”，让模型生成草稿后再次审核保存。

主简历是单份资源。再次导入时会替换主简历文件并刷新结构化内容，不会无限产生重复主简历。

## 网页填充流程

1. 进入“投递助手”。
2. 粘贴招聘官网地址，点击“打开官网”。
3. 在内置浏览器中自行登录，完成验证码和必要的人机验证。
4. 进入简历编辑页或信息预览页，等待页面完全加载。
5. 点击“扫描并智能填写”或“只填写空字段”。
6. 先由本地规则填写明确字段，再由模型识别剩余字段；页面底部会显示扫描、发送、实际填写和跳过的数量。
7. 填写完成后检查官网页面。遇到树形下拉、日期控件或自定义组件时，必要时手动修正。
8. 不要把“填写完成”误认为“申请已提交”；最终提交仍由你在官网完成。

### 常见网页问题

- **登录后页面空白或按钮点不了**：点击刷新，等待 Angular/React 页面完成渲染；必要时返回上一页重新进入编辑页。
- **扫描不到字段**：确认当前不是登录页或职位详情页，而是简历编辑/预览页；部分字段在 iframe 中，建议等待 2 秒后再次扫描。
- **日期或下拉框填不进去**：网站可能使用自定义控件。先检查资料库格式，再手动选择一次选项；不要强行把自由文本写进只能选择的控件。
- **同名字段被误判**：不要直接保存，双击该行打开大窗口，补充“教育经历/导师”“家庭成员”“项目证明人”等上下文后再选择目标位置。
- **身份证等敏感字段**：默认不会自动填写，也不会发送给模型。只有在个人资料页明确开启敏感信息自动填写后，才允许使用。

## 网页字段导出与资料库确认

点击“导出当前页面字段”后，审核窗口分为几种确认：

- **导出**：决定该字段是否进入本网址的导出文件。
- **加入资料库**：单独决定该字段是否写回个人资料库。导出本身不会默默覆盖资料库。
- **资料库状态**：显示“资料库已有”或“资料库缺少”，便于只补充缺失项。
- **合并到**：可选择基础资料中的具体字段、教育/工作/项目/奖项等经历类型、自定义字段或公司要求/待补资料；如果资料库已有多条教育、工作或项目记录，还可以直接选择某一条已有记录。
- **处理方式**：可写入个人资料库、记录为公司要求或不保存。

同一个网址会生成稳定文件名，例如：

```text
company_example_com-a1b2c3d4e5f6.json
company_example_com-a1b2c3d4e5f6.csv
```

再次从完全相同的网址导出时，旧字段会按“上下文 + 字段 + 目标位置”合并，重复字段更新，不会每次新增一个带时间戳的散文件。不同路径或查询参数会视为不同网址，避免把不同招聘流程混在一起。

建议的实际操作方式：

1. 第一次访问公司页面时先导出全部字段。
2. 在审核表中只勾选确认过的字段进入资料库。
3. 对已有字段选择“资料库已有”快速筛选，避免重复覆盖。
4. 对空字段选择“网页未填写”或“资料库缺少”，将其记录为待补要求。
5. 下次访问同一网址时，导出的文件会继续合并，资料库状态也会重新判断。

## AI 接口配置

打开“模型设置”，填写：

- **接口地址**：可以填写完整的 `/v1/chat/completions` 地址，也可以填写 Base URL，程序会尝试补全路径。
- **模型名称**：例如供应商提供的通用对话模型名称。
- **API Key**：只保存在本机 `profile.json`，不会提交到仓库。发布 GitHub 前必须删除本地资料库或确认它位于仓库之外。

模型主要用于：

- PDF/DOCX 结构化解析补充；
- 网页字段上下文识别和分类；
- 自我介绍润色；
- 从自然语言对话中提取资料；
- 总结公司要求和面试准备问题。

模型不可用时，应用仍会使用本地规则和审核窗口工作。模型返回内容只作为建议，必须经过用户确认。

## 投递记录

“投递记录”支持两种来源：

- **自动记录当前页面**：读取当前网址、页面标题和当前时间，之后由用户补充公司、职位和状态。
- **手动新增记录**：适合邮件、内推、线下招聘或无法扫描的官网。

记录字段包含公司、职位、申请入口、投递状态、投递日期、失败原因和备注。日期默认使用当天；失败原因可从预设选项中选择，也可以输入自定义原因。支持导出 CSV，便于备份和复盘。

## Windows EXE 打包

先完成依赖安装，再执行：

```powershell
cd C:\path\to\ResumeSubmitter\python_app
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```

生成目录通常为：

```text
python_app\dist\投了么\投了么.exe
```

这是目录模式打包，首次启动比单文件模式更稳定，尤其是 Qt WebEngine。发布给其他电脑时，建议压缩整个 `dist\投了么` 文件夹，而不是只复制 exe。

## GitHub 发布前检查

在上传前执行以下检查：

```powershell
cd C:\path\to\ResumeSubmitter
rg -n --hidden --glob '!python_app/build/**' --glob '!python_app/dist/**' --glob '!*\.pyc' "sk-[A-Za-z0-9]|Bearer |apiKey|phone|email|身份证|姓名" .
```

然后确认：

- 没有 `profile.json`、`applications.json`、`attachments/`、`company-records/`；
- 没有真实简历、身份证、电话、邮箱、API Key、浏览器 Cookie；
- 没有把 `RESUME_SUBMITTER_LIBRARY` 指向的资料目录复制进仓库；
- README 和示例只使用占位符；
- `build/`、`dist/`、`.venv/` 等构建目录不提交；
- 运行 `python -m py_compile` 检查源代码。

推荐的 Git 初始化流程：

```powershell
git init
git add .
git status
git commit -m "Initial Python version of 投了么"
git branch -M main
git remote add origin <你的 GitHub 仓库地址>
git push -u origin main
```

如果 `git status` 中出现个人资料文件，应立即停止提交，先移出仓库并检查 Git 历史；仅删除工作区文件并不能清除已经提交过的敏感信息。

## 开发与扩展

项目把可扩展注册表集中在 `profile_schema.py`：

- `PROFILE_FIELDS`：基础资料字段；
- `SECTION_FORMS`：教育、工作、项目、奖项、论文等重复经历；
- `APPLICATION_STATUSES`：投递状态；
- `FAILURE_REASONS`：失败原因；
- `PAGE_FIELD_CATEGORIES`：网页字段分类。

新增字段时，优先补充 schema、默认资料结构、资料库状态判断、审核目标选项和 README，而不是把字段名散落在界面代码中。网页解析器和模型分类都应保留本地兜底，避免模型不可用时界面卡死或丢失已扫描内容。

### 本地验证

```powershell
cd C:\path\to\ResumeSubmitter\python_app
python -m py_compile main.py browser_fill.py storage.py llm.py resume_parser.py profile_schema.py ui_theme.py
```

## 隐私与安全边界

- 所有资料默认保存在本机；只有在配置模型并触发模型功能时，相关文本才会发送到所配置的接口。
- 身份证号默认不自动填写、不发送给模型。
- 登录 Cookie 保存在本地浏览器资料目录，发布或备份项目时不要复制。
- 不要在 Issue、截图、日志或模型错误信息中粘贴 API Key、身份证、电话和邮箱。
- 导出文件可能包含公司页面字段和个人回答，分享前应再次脱敏。
- 未来如果增加云同步，应在同步前增加明确的加密、权限、冲突合并和删除机制；当前版本不提供云端同步。

## 故障反馈

反馈问题时请提供：

1. Windows 和 Python 版本；
2. 使用 PDF/DOCX/TXT 还是网页扫描；
3. 页面类型（登录页、编辑页、预览页）；
4. 不含个人信息的错误文本或截图；
5. 是否配置了模型接口，以及本地规则是否正常。

请先删除姓名、电话、邮箱、身份证、Cookie、API Key 和真实简历内容，再提交日志或截图。

## 许可证

当前仓库未预设许可证。公开发布前，请根据你的使用场景选择合适的开源许可证，并确认第三方依赖各自的许可条款。
