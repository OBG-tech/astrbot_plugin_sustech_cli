# astrbot_plugin_sustech_cli

AstrBot 插件：通过本机 [`sustech-cli`](https://github.com/OBG-tech/sustech-cli) 查询 SUSTech 个人课程信息（Blackboard DDL、TIS 课表、课程列表），并在严格的权限、路径和确认控制下支持附件下载、课表导出和 Blackboard 作业提交。

详细设计见 [`docs/designs/v1-design.md`](docs/designs/v1-design.md)。

## 功能

### 显式命令

| 命令 | 说明 |
| --- | --- |
| `/sustech-status` | 检查 CLI、凭证 profile 和认证状态 |
| `/sustech-ddl [days] [course]` | 查询未来 N 天 Blackboard DDL（默认 14 天） |
| `/sustech-schedule [date]` | 查询 TIS 课表 |
| `/sustech-courses [query]` | 查询 Blackboard 课程列表 |
| `/sustech-download <course_id> <content_id> <attachment_id>` | 下载附件到受控输出目录 |
| `/sustech-calendar-export` | 导出课表为 iCalendar 文件 |
| `/sustech-submit-preview <course_id> <content_id\|-> <file> [comment]` | 生成作业提交预览（不提交） |
| `/sustech-submit-confirm <token>` | 对已确认的预览执行提交 |

### LLM Tools

`sustech_get_deadlines`、`sustech_get_schedule`、`sustech_get_courses`、`sustech_download_attachment`、`sustech_export_calendar`、`sustech_prepare_assignment_submission`。

其中 `sustech_prepare_assignment_submission` 只能生成预览；真正的提交必须由用户显式执行 `/sustech-submit-confirm <token>`。

### 第一版不支持

登录/登出、任意 Shell 命令执行、自动选课、成绩修改、公开群聊中的个人信息广播，以及设计文档 §4.3 列出的全部写操作（`bb message-send`、`booking create`、`tis selection apply` 等）。

## 安全模型

- **访问控制**：默认 `private_only=true` 且 `allowed_users=[]`，白名单为空时拒绝一切个人信息查询；所有命令与 LLM Tool 走同一套检查。
- **主密码**：只通过子进程环境变量 `SUSTECH_MASTER_PASSWORD` 注入 `sustech` 进程，绝不出现在命令参数、聊天、日志或 LLM 上下文中。
- **命令白名单**：Runner 只接受内部固定的 operation，使用 `asyncio.create_subprocess_exec`（无 shell），不存在任意命令执行入口。
- **文件路径**：下载/导出只能写入 `download_root`，提交输入只能来自 `input_root`；均经过 `resolve()` 与符号链接检查，默认不覆盖已有文件，单文件大小受 `max_download_bytes` 限制。
- **两阶段提交**：预览 → 展示课程/作业/文件/大小/SHA-256/迟交状态 → 短期一次性确认 token（默认 10 分钟，绑定用户、会话、文件哈希，重启即失效）→ 确认阶段重算哈希一致才执行 `bb submit apply --confirm`。返回 `DO_NOT_RETRY_AUTOMATICALLY` 或结果不确定时绝不自动重试。

## 安装

1. 安装 `sustech-cli`（在其源码目录）：

   ```bash
   npm install && npm run build && npm install -g .
   which sustech && sustech --version
   ```

2. 安装插件：

   ```bash
   cd /path/to/AstrBot/data/plugins
   git clone <plugin-repository-url> astrbot_plugin_sustech_cli
   ```

3. 在 AstrBot WebUI 配置插件（Extensions → Plugins），关键配置项：

   ```text
   sustech_command: /usr/local/bin/sustech   # 建议绝对路径，systemd/Docker 不继承 Shell PATH
   master_password: <填写你的主密码>        # secret 字段，仅用于子进程环境变量
   profile: default
   private_only: true
   allowed_users: [<管理员用户 ID>]
   download_root: data/sustech-cli/files
   input_root: data/sustech-cli/inputs
   ```

4. 配置文件安全：`secret: true` 只在 WebUI 遮挡输入，配置文件仍是明文。部署后：

   ```bash
   chmod 600 AstrBot/data/config/astrbot_plugin_sustech_cli_config.json
   ```

   并确保版本库忽略 `data/config/` 与 `*_config.json`。

5. 重新加载插件（WebUI → Reload）或重启 AstrBot。

## 测试

零第三方依赖（标准库 `unittest` + fake CLI）：

```bash
python3 -m unittest discover -s tests -v
```

## 手动验收

先在服务器上直接验证 CLI（导出 `SUSTECH_MASTER_PASSWORD` 后执行 `sustech auth status --json`、`sustech bb deadlines --days 14 --json` 等，见设计文档 §13.5），再在 AstrBot 中依次测试显式命令、自然语言查询和负面场景（未授权用户、群聊下载、任意路径写入、文件替换后确认、token 过期、提交结果不确定）。
