# Canvas_helper

> 本地优先的 Canvas 课程资料同步、备份、检索与 AI 分析助手——只读访问 Canvas，凭据只保留在本机。

Canvas_helper 是一个本地 Canvas 课程资料助手，用来同步课程、公告、作业、页面、成员和文件索引，并把课件安全地备份到本机。它提供 React 前端、FastAPI 后端、SQLite 本地缓存、文件文本抽取/OCR，以及可选的 OpenAI-compatible AI 分析能力。

## 项目特点

- 只读 Canvas 访问：后端 Canvas 客户端只允许 `GET` 和 `HEAD`，Token 不会暴露给浏览器。
- 增量同步与本地备份：课程元数据、课件索引和选中文件可保存到本地 `data/` 目录。
- 资料解析：支持 PDF、PPTX、DOCX、HTML、文本、ZIP 等资料的文本抽取，PDF 可按配置启用 OCR。
- AI 课程分析：可接入 OpenAI-compatible API，把本地缓存和已抽取文本整理成课程时间线、摘要和问答上下文。
- 通知能力：支持 Telegram 和邮件提醒；未配置 SMTP 时邮件会写入本地 outbox。
- Windows 友好：内置启动脚本和计划任务脚本，可一键启动或登录后自动启动。

## 技术栈

- 后端：FastAPI、httpx、pydantic-settings、SQLite
- 文件解析：PyMuPDF、python-pptx、python-docx、BeautifulSoup、Pillow、pytesseract（OCR）
- 前端：React 19 + TypeScript、Vite、Tailwind CSS、lucide-react
- AI：OpenAI-compatible `/v1/chat/completions` 接口
- 测试：pytest、pytest-asyncio、FastAPI TestClient、TypeScript build

## 安装

需要先安装 Python 3.11+、Node.js LTS 和 npm。OCR 是可选能力，如需识别扫描版 PDF，请额外安装 Tesseract OCR。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
npm --prefix frontend install
Copy-Item .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
CANVAS_BASE_URL=https://your-school.instructure.com/
CANVAS_API_TOKEN=
```

如需 AI 分析，再填写：

```dotenv
OPENAI_COMPAT_BASE_URL=https://api.example.com/v1
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_MODEL=gpt-4.1-mini
```

## 使用方式

推荐直接运行：

```powershell
.\start.ps1
```

也可以使用 npm 同时启动前后端：

```powershell
npm run dev
```

默认访问地址：

- 前端：<http://127.0.0.1:5173>
- 后端健康检查：<http://127.0.0.1:8000/api/health>

第一次同步会下载和解析课程资料，耗时取决于课程数量、文件大小和 OCR 配置。

## 计划任务

注册一个登录后自动启动的 Windows 计划任务：

```powershell
.\scripts\install-scheduled-task.ps1 -StartNow
```

改成每天固定时间启动：

```powershell
.\scripts\install-scheduled-task.ps1 -Trigger Daily -At 09:00
```

卸载计划任务：

```powershell
.\scripts\uninstall-scheduled-task.ps1
```

## 测试

```powershell
pip install -r requirements-dev.txt
pytest
npm --prefix frontend run build
```

## 文档

系统架构、模块职责、数据模型、接口契约与安全边界见 [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md)。

## 安全与公开仓库说明

公开仓库只应提交源码、文档和依赖清单。`.env`、本地数据库、日志、同步下来的课程资料、虚拟环境和 `node_modules` 都已在 `.gitignore` 中排除。提交前建议再次运行敏感信息扫描，确认没有 Token、API Key、课程文件或个人数据进入 Git 历史。

## 免责声明

本项目是个人开发的非官方工具，与 Instructure / Canvas 没有任何关联。它只通过官方 Canvas API 进行只读访问；请遵守所在院校的使用条款，妥善保管自己的 API Token，并仅同步你有权访问的课程资料。
