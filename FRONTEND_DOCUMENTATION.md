# Canvas_helper 前端技术文档

本文档详细说明了 Canvas_helper 前端工程的架构设计、目录结构、状态管理、路由设计以及核心技术实现。旨在帮助后续开发者快速理解前端代码库并参与开发。

## 1. 架构概述

Canvas_helper 前端采用现代化 Web 技术栈构建，是一个**单页面应用 (SPA)**：

*   **核心框架**：React 19
*   **开发语言**：TypeScript 5.7+，提供严格的静态类型检查
*   **构建工具**：Vite 6，提供极速的冷启动和热更新 (HMR) 体验
*   **样式方案**：Tailwind CSS 3.4+，采用 Utility-first 理念进行快速响应式布局开发，搭配自定义 CSS (`styles.css`) 实现底层视觉基调（高对比度、复古极简风）
*   **图标库**：`lucide-react`
*   **路由管理**：`react-router-dom` v7
*   **网络请求**：基于原生 `fetch` API 封装的轻量级客户端

## 2. 目录结构

所有前端源代码均位于 `frontend/src/` 目录下：

```text
frontend/src/
├── api/          # 封装的后端 API 请求函数 (agent.ts, client.ts, courses.ts 等)
├── components/   # 可复用的通用 UI 组件 (MarkdownContent, SyncProgressBar 等)
├── context/      # React Context，用于全局状态共享 (AppContext.tsx)
├── hooks/        # 自定义 React Hooks，封装复杂业务逻辑 (useCanvasData.ts 等)
├── i18n/         # 轻量级多语言国际化实现
├── types/        # TypeScript 全局类型定义 (View, Course, Status 等)
├── utils/        # 工具函数 (数据格式化 format.ts, 进度解析 progress.ts 等)
├── views/        # 页面级路由视图组件 (DashboardView, CourseDetailView 等)
├── App.tsx       # 根组件，负责路由配置和全局 Layout
└── main.tsx      # 应用入口文件，挂载 React 根节点
```

## 3. 核心数据流与状态管理

前端没有引入 Redux 等重型状态管理库，而是采用 **Context API + 自定义 Hooks** 的轻量级方案：

### 3.1 全局 Context (`AppContext.tsx`)
提供跨组件层级的核心状态：
*   `lang`: 当前语言 (中/英)
*   `query`: 顶栏的全局搜索关键词
*   `canvasBaseUrl`: 从后端加载的 Canvas 基础地址
*   `error` / `busy`: 全局错误提示与页面全局 Loading 状态

### 3.2 业务逻辑 Hooks (`useCanvasData.ts`)
这是前端最核心的 Hook，负责协调与课程相关的所有状态：
*   **数据获取**：拉取课程列表 (`courses`)、选中课程的详细资料 (`detail`)。
*   **同步与轮询**：管理 `syncStatus`（同步进度）和 `analysisStatus`（分析进度）。当触发同步或分析操作时，该 Hook 内部会自动启动轮询定时器，定时调用后端 API 获取最新进度，并更新到 UI 上，直到任务完成 (`DONE`, `FAILED`, `CANCELLED`)。
*   **本地状态**：管理当前选中的课程 (`selectedCourse`) 和标签页 (`activeTab`)。

### 3.3 辅助 Hooks
*   `useTermGroups.ts`：负责将扁平的课程列表按学期 (Term) 分组，并管理侧边栏的折叠/展开状态。
*   `useAnnouncementsSeen.ts`：通过 `localStorage` 记录用户已读的公告数量，用于在 UI 上显示“红点”或“New”徽章。
*   `useExternalLinkHardening.ts`：统一拦截外链点击，安全地在系统浏览器或新标签页中打开。

## 4. 路由设计

采用 `react-router-dom` 的 `<Routes>` 组件进行声明式路由，所有视图挂载在 `<App />` 的 `app-content` 区域：

*   `/`：**DashboardView**（首页仪表盘），展示所有课程卡片、全局同步进度。
*   `/course/:courseId`：**CourseDetailView**（课程详情页），内含多标签页（资料、作业、公告、日历、成员、分析），用于展示单一课程的所有同步数据。
*   `/agent`：**AgentChatView**（AI 助手界面），提供与本地化大模型的对话能力，支持指定上下文课程。
*   `/settings`：**SettingsView**（配置页），用于管理 Token、接口地址、并发数等后端配置。

## 5. API 与网络请求

网络请求逻辑集中在 `src/api/` 目录，底层基于 `src/api/client.ts` 封装的 `api<T>` 函数：

*   **错误处理拦截**：统一捕获非 2xx 状态码，并尝试解析后端的 JSON 错误体（如 FastAPI 的 `detail` 字段）。
*   **文件下载**：针对二进制流下载（如打包下载课件），采用 `fetch` 获取 Blob，并通过 `window.URL.createObjectURL(blob)` 创建临时下载链接模拟点击触发下载。

## 6. 组件设计与样式

*   **响应式 Layout**：`App.tsx` 实现了一个经典的左侧边栏（或顶部移动端导航）+ 右侧主内容区的布局。通过媒体查询和 Tailwind 的断点（如 `hidden md:flex`）实现 Mobile/Desktop 视图切换。
*   **UI 风格**：大量使用 `border-black`、单色填充（`bg-black`、`bg-[#F4F4F0]`）、等宽字体 (`font-mono`) 和粗体大写字母 (`tracking-widest uppercase`)，塑造了一种硬核、极简的控制台/实验仪器视觉风格。
*   **富文本与 Markdown**：利用 `MarkdownContent` 和 `SafeHtmlContent` 组件，安全地渲染从 Canvas 抓取的 HTML 内容以及大模型返回的 Markdown，并内置样式隔离，防止污染全局样式。

## 7. 国际化 (i18n)

由于功能精简，项目实现了内置的轻量级 i18n 方案 (`src/i18n/index.ts`)：
*   支持 `en` 和 `zh` 双语字典。
*   通过 `AppContext` 向下传递 `t(key)` 翻译函数。
*   语言切换实时生效，无需刷新页面。
