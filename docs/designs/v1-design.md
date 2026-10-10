# AstrBot SUSTech CLI 插件第一版设计

## 1. 文档信息

- **插件名称**：`astrbot_plugin_sustech_cli`
- **设计版本**：v1 MVP
- **目标运行环境**：AstrBot + Linux + 已安装的 `sustech-cli`
- **设计状态**：待实现
- **关联项目**：[`sustech-cli`](https://github.com/OBG-tech/sustech-cli)
- **关联修复提交**：`dba428d`

## 2. 背景与目标

本插件用于让 AstrBot 机器人通过本机的 `sustech-cli` 查询 SUSTech 个人课程信息，并将结果转换为适合聊天场景阅读的中文消息。

第一版实现课程信息查询，并在严格的权限、路径和确认控制下支持文件操作与 Blackboard 作业提交。

第一版不实现：

- 登录和登出；
- 任意 Shell 命令执行；
- 自动选课；
- 成绩修改；
- 公开群聊中的个人信息广播。

第一版条件支持：

- Blackboard 附件下载到受控的插件输出目录；
- TIS iCalendar 或查询报告写入受控的插件输出目录；
- Blackboard 作业提交，但必须经过预览、哈希校验、用户明确确认和 `--confirm`；
- 所有文件路径都必须位于配置的输入或输出目录内，不允许任意路径写入。

目标使用场景：

```text
用户：帮我查未来两周有哪些课程 DDL？
机器人：调用 sustech_get_deadlines 工具
插件：执行 sustech bb deadlines --days 14 --json
机器人：格式化 JSON 并输出中文结果
```

## 3. 方案选择

### 3.1 推荐方案：AstrBot 插件调用本机 CLI

插件通过 `asyncio.create_subprocess_exec` 启动本机 `sustech` 命令，使用 JSON 输出作为稳定接口。

```text
AstrBot
  └── astrbot_plugin_sustech_cli
        └── 受限 CLI Runner
              └── sustech bb deadlines --json
```

选择该方案的原因：

1. 复用 `sustech-cli` 已有的认证、凭证存储和服务适配逻辑；
2. 不需要在 AstrBot 插件中重复实现 CAS、Blackboard 或 TIS 登录；
3. CLI 已经提供稳定的 JSON 输出契约；
4. 可以通过固定的命令映射限制执行范围；
5. 主密码只需要注入 `sustech` 子进程环境变量。

### 3.2 不直接使用现有 MCP

`sustech-cli` 当前的 `sustech-mcp` 是本地 stdio MCP 服务，但其安全边界明确排除了：

- Blackboard 认证数据；
- TIS 认证数据；
- 成绩、课表等个人数据；
- 登录、凭证和本地私密状态。

因此第一版不通过现有 MCP 查询个人课程信息，而是由 AstrBot 插件直接调用 CLI。

后续如果需要 MCP，应作为独立的认证服务接口重新设计，不能直接开放一个通用 Shell 或通用 CLI 执行工具。

## 4. 第一版功能范围

### 4.1 显式命令

第一版提供以下显式命令：

| AstrBot 命令 | 实际 CLI | 说明 |
| --- | --- | --- |
| `/sustech-status` | `sustech auth status --json` | 检查 CLI、凭证 profile 和认证状态 |
| `/sustech-ddl` | `sustech bb deadlines --days N --json` | 查询未来 N 天 Blackboard DDL |
| `/sustech-schedule` | `sustech tis schedule --json` | 查询当前 TIS 课表 |
| `/sustech-courses` | `sustech bb courses --json` | 查询 Blackboard 课程列表 |
| `/sustech-download` | `sustech bb download ... --destination PATH` | 下载 Blackboard 附件到受控输出目录 |
| `/sustech-calendar-export` | `sustech tis ical --destination PATH` | 将课表/考试等导出到受控输出目录 |
| `/sustech-submit-preview` | `sustech bb submit preview ...` | 生成作业提交预览，不执行提交 |
| `/sustech-submit-confirm` | `sustech bb submit apply ... --confirm` | 仅对已确认的预览执行提交 |

建议支持的查询参数：

```text
/sustech-ddl
/sustech-ddl 30
/sustech-ddl 14 机器学习
/sustech-schedule
/sustech-schedule 2026-10-12
/sustech-courses
/sustech-courses 计算机
```

文件操作命令不允许用户自由指定任意目标路径。插件应自动生成位于配置目录下的目标路径，并返回下载结果或生成的文件。

作业提交命令必须采用两步流程：

```text
/sustech-submit-preview ...
/sustech-submit-confirm TOKEN
```

如果 AstrBot 命令参数解析对中文或可选参数支持不稳定，第一版可以先实现无参数命令，再通过 LLM Tool 支持复杂查询参数。

### 4.2 LLM Tools

第一版注册以下 LLM Tools：

```text
sustech_get_deadlines
sustech_get_schedule
sustech_get_courses
sustech_download_attachment
sustech_export_calendar
sustech_prepare_assignment_submission
```

其中：

- `sustech_download_attachment` 可以执行只读远程下载并写入受控目录；
- `sustech_export_calendar` 可以执行本地文件写入；
- `sustech_prepare_assignment_submission` 只能生成预览，不能直接调用 `apply`；
- 作业真正提交必须由显式确认命令完成。

建议的自然语言映射：

| 用户意图 | 工具 | 命令 |
| --- | --- | --- |
| 课程 DDL、作业截止时间 | `sustech_get_deadlines` | `bb deadlines` |
| 今天或某天的课表 | `sustech_get_schedule` | `tis schedule` |
| Blackboard 课程列表 | `sustech_get_courses` | `bb courses` |
| 下载某门课的附件 | `sustech_download_attachment` | `bb download` |
| 导出课表文件 | `sustech_export_calendar` | `tis ical` |
| 准备作业提交 | `sustech_prepare_assignment_submission` | `bb submit preview` |

第二版再增加：

```text
sustech_get_assignments
sustech_get_exams
sustech_get_announcements
sustech_get_grades
```

### 4.3 第一版仍然禁止的功能

第一版明确禁止以下功能：

```text
bb message-send
booking create
booking cancel
lib-booking create
pms upload
pms delete
tis plan init
tis plan add
tis plan remove
tis selection apply
tis enroll apply
tis bid apply
auth login
auth logout
```

`bb submit apply` 不作为 LLM Tool 暴露，只能由已绑定用户、已过期检查和哈希校验通过的确认流程调用。

插件不能将用户输入直接拼接成 CLI 命令，也不能提供类似下面的接口：

```text
sustech_run(command: string)
```

插件不能将用户输入直接拼接成 CLI 命令，也不能提供类似下面的接口：

```text
sustech_run(command: string)
```

## 5. 项目结构

建议目录结构：

```text
astrbot_plugin_sustech_cli/
├── metadata.yaml
├── _conf_schema.json
├── main.py
├── runner.py
├── access.py
├── formatter.py
├── errors.py
├── requirements.txt
├── README.md
├── docs/
│   └── designs/
│       └── v1-design.md
└── tests/
    ├── test_runner.py
    ├── test_access.py
    └── test_formatter.py
```

如果第一版完全使用 Python 标准库，`requirements.txt` 可以为空或暂不创建。

AstrBot 插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_sustech_cli/
```

AstrBot 配置目录：

```text
AstrBot/data/config/astrbot_plugin_sustech_cli_config.json
```

## 6. 配置设计

### 6.1 `_conf_schema.json`

建议内容：

```json
{
  "sustech_command": {
    "type": "string",
    "description": "sustech CLI 可执行文件路径",
    "hint": "AstrBot 运行环境无法通过 PATH 找到 sustech 时，请填写绝对路径",
    "default": "sustech"
  },
  "master_password": {
    "type": "string",
    "description": "SUSTech 加密凭证存储主密码",
    "hint": "只用于启动 sustech 子进程，不得输出到聊天、日志或大模型上下文",
    "secret": true,
    "default": ""
  },
  "profile": {
    "type": "string",
    "description": "使用的 sustech credential profile",
    "default": "default"
  },
  "timeout_seconds": {
    "type": "int",
    "description": "单次 CLI 查询超时时间",
    "default": 45
  },
  "default_deadline_days": {
    "type": "int",
    "description": "默认查询未来多少天的 DDL",
    "default": 14
  },
  "private_only": {
    "type": "bool",
    "description": "是否只允许私聊查询个人课程信息",
    "default": true
  },
  "allowed_users": {
    "type": "list",
    "description": "允许查询个人课程信息的用户 ID 列表",
    "default": []
  },
  "enable_llm_tools": {
    "type": "bool",
    "description": "是否允许大模型自动调用 SUSTech 查询工具",
    "default": true
  },
  "allow_file_operations": {
    "type": "bool",
    "description": "是否允许附件下载和受控本地文件写入",
    "default": true
  },
  "download_root": {
    "type": "string",
    "description": "插件下载和导出文件的根目录",
    "hint": "所有输出路径必须位于该目录内，建议使用 AstrBot data 目录下的专用子目录",
    "default": "data/sustech-cli/files"
  },
  "input_root": {
    "type": "string",
    "description": "允许作业提交读取的本地输入文件根目录",
    "hint": "插件不得读取该目录之外的文件",
    "default": "data/sustech-cli/inputs"
  },
  "max_download_bytes": {
    "type": "int",
    "description": "单个下载文件的最大字节数",
    "default": 52428800
  },
  "allow_assignment_submission": {
    "type": "bool",
    "description": "是否允许作业提交预览和确认流程",
    "default": true
  },
  "confirmation_ttl_seconds": {
    "type": "int",
    "description": "作业提交预览确认令牌的有效时间",
    "default": 600
  }
}
```

### 6.2 主密码处理

`sustech-cli` 的 `linux-encrypted-file` 后端不会将主密码保存到磁盘。插件每次启动 CLI 子进程时需要设置：

```text
SUSTECH_MASTER_PASSWORD=<configured master password>
```

同时设置 profile：

```text
SUSTECH_PROFILE=<configured profile>
```

不要将主密码放在：

- CLI 参数；
- 用户消息；
- LLM Tool 参数；
- 日志；
- 错误消息；
- 返回给模型的 JSON；
- Git 仓库；
- README 或示例配置。

### 6.3 配置文件安全

`secret: true` 只负责在 AstrBot 管理界面遮挡输入，不代表配置文件加密。配置文件中的主密码仍可能以明文存在。

部署后需要：

```bash
chmod 600 AstrBot/data/config/astrbot_plugin_sustech_cli_config.json
```

并确保：

```gitignore
data/config/
*_config.json
```

后续生产方案可以将配置改为保存主密码文件路径：

```json
{
  "master_password_file": "/run/secrets/sustech_master_password"
}
```

第一版为了简化配置，允许直接保存 `master_password`，但必须在 README 和 WebUI 提示中明确说明其安全含义。

## 7. CLI Runner 设计

### 7.1 Runner 职责

`runner.py` 负责：

1. 读取插件配置；
2. 校验配置；
3. 校验允许执行的命令；
4. 构造固定的 CLI 参数；
5. 设置子进程环境变量；
6. 启动 `sustech`；
7. 读取 stdout 和 stderr；
8. 处理超时；
9. 解析 JSON；
10. 将错误转换为插件内部错误。

### 7.2 子进程调用要求

必须使用：

```python
asyncio.create_subprocess_exec
```

禁止使用：

```python
shell=True
```

建议逻辑：

```python
env = os.environ.copy()
env["SUSTECH_MASTER_PASSWORD"] = master_password
env["SUSTECH_PROFILE"] = profile

process = await asyncio.create_subprocess_exec(
    command,
    *args,
    "--json",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env=env,
)
```

如果配置中的主密码为空，不能让子进程进入交互式等待。应在插件内部提前返回：

```text
SUSTECH 主密码尚未配置，请在 AstrBot 插件设置中填写。
```

### 7.3 超时和并发

默认超时时间：45 秒。

超过超时时间后：

1. 终止子进程；
2. 等待进程退出；
3. 返回“查询超时”；
4. 不把完整 stderr 发给用户。

建议使用：

```python
asyncio.Semaphore(1)
```

限制同一时间只执行一个 `sustech` 查询，避免重复访问 Blackboard 或 TIS。

### 7.4 命令白名单

Runner 不接受任意字符串，只接受内部定义的 operation：

```python
ALLOWED_OPERATIONS = {
    "status",
    "deadlines",
    "schedule",
    "courses",
    "download_attachment",
    "export_calendar",
    "submit_preview",
    "submit_apply",
}
```

然后由插件内部将 operation 映射到参数：

```python
"deadlines" -> ["bb", "deadlines", "--days", str(days)]
"schedule"  -> ["tis", "schedule"]
"courses"   -> ["bb", "courses"]
"download_attachment" -> ["bb", "download", course_id, content_id, attachment_id, "--destination", destination]
"export_calendar" -> ["tis", "ical", "--destination", destination]
"submit_preview" -> ["bb", "submit", "preview", ...]
"submit_apply" -> ["bb", "submit", "apply", ..., "--expected-sha256", sha256, "--confirm"]
```

`submit_apply` 只能由插件内部的确认流程生成，不能接受 LLM 直接传入的任意参数。

不能直接接受：

```python
run(user_command: str)
```

### 7.5 文件下载和本地写入

第一版允许以下文件操作：

```text
bb download
bb attempt-download
bb sync
bb calendar-link fetch
tis ical --destination PATH
profile export
academic snapshot save
```

插件只开放明确的文件用途，不开放通用文件读写工具。所有输出文件必须位于配置的 `download_root` 目录内。

下载路径处理要求：

1. 不接受任意绝对路径作为目标；
2. 对远程文件名进行安全清理；
3. 删除路径分隔符和 `..` 片段；
4. 使用 `Path.resolve()` 检查最终路径仍位于 `download_root`；
5. 检查父目录是否存在符号链接逃逸；
6. 默认禁止覆盖已有文件；
7. 单文件大小不得超过 `max_download_bytes`；
8. 下载中途失败时删除不完整文件；
9. 不把本地绝对路径发送给不必要的群聊成员。

建议路径格式：

```text
data/sustech-cli/files/<operation>/<date>/<safe-file-name>
```

`download_root` 目录不存在时由插件创建，并使用尽可能严格的文件权限。插件应返回下载结果，包括文件名、大小和 SHA-256，而不是直接返回任意内部路径。

如果当前 AstrBot 适配器支持文件消息，插件可以将下载文件作为文件消息发送；不支持时返回受控目录下的相对文件名。

### 7.6 作业提交流程

作业提交不是普通的 LLM Tool 直接执行，而是两阶段确认流程：

```text
用户请求提交作业
        ↓
权限检查
        ↓
检查输入文件位于 input_root 内
        ↓
计算本地文件 SHA-256
        ↓
sustech bb submit preview
        ↓
向用户展示课程、作业、文件名、大小、哈希、迟交状态
        ↓
生成短期 confirmation token
        ↓
用户明确确认
        ↓
再次检查用户、会话、文件哈希和 token
        ↓
sustech bb submit apply --expected-sha256 HASH --confirm
        ↓
读取提交结果并返回
```

`confirmation token` 必须绑定：

- 用户 ID；
- 会话 ID；
- 课程 ID；
- Blackboard 内容 ID 或 column ID；
- 输入文件路径；
- 输入文件 SHA-256；
- 预览创建时间；
- `confirmation_ttl_seconds`。

确认令牌默认 10 分钟后失效。插件重启后所有内存令牌失效。

LLM Tool 只允许执行：

```text
sustech bb submit preview
```

LLM Tool 不允许直接执行：

```text
sustech bb submit apply
```

真正的 `apply` 只能由显式确认命令或等价的明确用户交互触发。

作业输入文件只能来自：

1. AstrBot 已接收并复制到插件临时输入目录的附件；或
2. `input_root` 目录下的本地文件。

不得读取 `input_root` 之外的任意路径。提交后可以根据配置删除临时文件；默认建议保留短时间用于失败诊断，但不得输出文件内容。

如果 CLI 返回 `DO_NOT_RETRY_AUTOMATICALLY` 或提交结果不确定，插件不得自动重试，只能提示用户手动检查 Blackboard 状态。

## 8. 命令参数校验

### 8.1 DDL 参数

```text
days: 1-90 的整数
course: 可选，最大长度 100
submission_state: 固定枚举
```

允许的 `submission_state`：

```text
not_attempted
in_progress
submitted
completed
mixed
other
```

### 8.2 课表参数

支持：

```text
semester: YYYY-YYYY-N
week: 正整数
date: YYYY-MM-DD
all: boolean
```

以下参数互斥：

```text
week
 date
 all
```

### 8.3 课程搜索参数

```text
query: 可选，最大长度 100
```

### 8.4 文件参数

```text
course_id: 非空 opaque token
content_id: 非空 opaque token
attachment_id: 非空 opaque token
```

文件参数必须进行长度和字符集校验，不能包含路径分隔符、Shell 元字符或换行。

下载目标由插件生成，不允许从用户输入中直接接受任意 `--destination`。插件应使用配置的 `download_root`，并在目录下创建以操作和日期区分的子目录。

### 8.5 作业提交参数

```text
course_id: 非空 opaque token
content_id 或 column_id: 二选一
file: 必须位于 input_root 或插件临时输入目录
comment: 可选，最大长度 2000
allow_late: 默认 false，只有用户明确要求时才允许
```

提交前必须计算 SHA-256，并确保预览阶段和确认阶段的文件哈希一致。文件被替换、修改、移动或 token 过期时，必须要求重新执行 preview。

所有字符串参数都需要去除首尾空白，并限制长度。

## 9. AstrBot Handler 设计

### 9.1 插件初始化

`main.py` 中的插件类应：

```python
class SustechCliPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runner = SustechRunner(config)
```

初始化时不要自动请求 Blackboard 或 TIS，只进行本地配置检查。

### 9.2 LLM Tool

使用 AstrBot 当前推荐的 `@filter.llm_tool`。

工具 docstring 必须包含符合格式的 `Args:`，例如：

```python
@filter.llm_tool(name="sustech_get_deadlines")
async def get_deadlines(
    self,
    event: AstrMessageEvent,
    days: int = 14,
    course: str = "",
):
    """查询当前账号的 Blackboard 课程 DDL。

    Args:
        days(number): 查询未来多少天，范围为 1 到 90。
        course(string): 可选的课程名称或关键词。
    """
```

工具执行前必须经过访问权限检查。

### 9.3 显式命令

第一版可以使用以下命令：

```python
@filter.command("sustech-ddl")
@filter.command("sustech-schedule")
@filter.command("sustech-courses")
@filter.command("sustech-status")
@filter.command("sustech-download")
@filter.command("sustech-calendar-export")
@filter.command("sustech-submit-preview")
@filter.command("sustech-submit-confirm")
```

文件下载和导出命令必须先执行权限检查与目录检查。

作业提交命令必须使用两阶段状态机：

```text
preview -> pending_confirmation -> confirmed/apply
```

`/sustech-submit-confirm` 只接受插件生成的短期 token，不接受用户直接传入 `--confirm` 或任意 CLI 参数。

如果需要参数，应优先使用 AstrBot 的命令参数解析机制；无法可靠解析时，在插件内部进行有限的参数解析。

## 10. 权限与隐私

### 10.1 默认限制

默认配置：

```text
private_only = true
allowed_users = []
```

建议第一版在 `allowed_users` 为空时拒绝执行个人信息查询，而不是默认放开。

### 10.2 访问规则

按以下顺序检查：

1. 检查当前消息是否为私聊；
2. 如果 `private_only=true`，群聊直接拒绝；
3. 如果 `allowed_users` 非空，检查当前发送者 ID；
4. 不在白名单时拒绝；
5. 权限通过后才读取主密码和执行 CLI。

拒绝消息：

```text
该功能只允许机器人管理员或授权用户使用。
```

不要告诉未授权用户课程数据是否存在。

### 10.3 文件操作权限

文件下载、文件导出和作业提交都属于个人数据操作，必须使用与课程查询相同的私聊和用户白名单检查。

作业提交还必须满足：

1. `allow_assignment_submission=true`；
2. 当前用户具有提交权限；
3. 输入文件位于 `input_root` 或插件临时输入目录；
4. 当前用户确认的 preview token 未过期；
5. 确认阶段重新计算的文件 SHA-256 与预览阶段一致；
6. 课程 ID、内容 ID、文件和会话与 token 完全匹配。

文件下载和导出必须满足：

1. `allow_file_operations=true`；
2. 目标文件位于 `download_root`；
3. 文件大小不超过 `max_download_bytes`；
4. 默认不覆盖已有文件；
5. 不将个人文件发送到未授权群聊。

### 10.4 日志要求

不得记录：

- `master_password`；
- 完整 `os.environ`；
- CLI 参数中的敏感字段；
- 凭证内容；
- 课程查询返回的完整个人数据；
- 完整 stderr。

可以记录：

```text
operation=deadlines
user_id=<脱敏或哈希后的 ID>
elapsed_ms=1234
exit_code=0
```

## 11. 错误映射

需要识别以下 `sustech-cli` 错误码：

```text
MASTER_PASSWORD_REQUIRED
MASTER_PASSWORD_INVALID
CREDENTIAL_PROFILE_NOT_FOUND
CREDENTIAL_STORE_UNAVAILABLE
CREDENTIAL_STORE_TIMEOUT
CREDENTIALS_REQUIRED
```

建议映射为：

| 错误码 | 用户可见提示 |
| --- | --- |
| `MASTER_PASSWORD_REQUIRED` | 插件未配置 SUSTech 主密码 |
| `MASTER_PASSWORD_INVALID` | SUSTech 主密码不正确 |
| `CREDENTIAL_PROFILE_NOT_FOUND` | 当前 profile 尚未登录，请先配置 SUSTech 凭证 |
| `CREDENTIAL_STORE_UNAVAILABLE` | 本地凭证存储后端不可用 |
| `CREDENTIAL_STORE_TIMEOUT` | 本地凭证存储响应超时 |
| `CREDENTIALS_REQUIRED` | 尚未配置 SUSTech 登录凭证 |
| 其他错误 | SUSTech 查询失败，请稍后重试 |

在聊天中返回经过截断和脱敏的 CLI 原始错误摘要（含错误码和退出码），但不返回密码、Cookie、令牌、Node.js 堆栈或完整本地路径。

## 12. 结果格式化

CLI 返回 JSON 后，插件应提取必要字段，再转换成聊天消息。

### 12.1 DDL 格式

```text
未来 14 天共有 3 项课程 DDL：

1. 高等数学
   截止时间：2026-10-10 23:59
   状态：未提交

2. 机器学习
   截止时间：2026-10-13 23:59
   状态：进行中
```

无结果：

```text
未来 14 天没有发现课程 DDL。
```

### 12.2 课表格式

```text
2026-10-12 星期一课表：

08:00-09:40  高等数学
地点：第一教学楼 101
教师：某某老师

14:00-15:40  机器学习
地点：科研楼 302
教师：某某老师
```

### 12.3 课程列表格式

```text
当前 Blackboard 课程：

1. 机器学习
2. 高等数学
3. 数据结构
```

不要直接输出完整原始 JSON。

### 12.4 文件下载结果

下载成功时返回：

```text
文件下载成功：
文件名：lecture-notes.pdf
大小：2.4 MB
SHA-256：<hash>
```

如果当前消息适配器支持文件消息，插件可以继续发送文件；否则只返回受控输出目录下的文件标识。

下载失败时返回错误码、CLI 退出码和经过截断/脱敏的原始错误摘要；不返回密码、Cookie、令牌、堆栈或完整本地路径。

### 12.5 作业预览结果

预览阶段必须展示：

```text
请确认是否提交以下作业：
课程：机器学习
作业：Assignment 1
文件：answer.pdf
大小：1.8 MB
SHA-256：<hash>
是否迟交：否

如需提交，请回复：
/sustech-submit-confirm <token>
```

预览不会执行远程提交。

### 12.6 作业提交结果

成功提交时返回：

```text
作业提交成功。
课程：机器学习
作业：Assignment 1
提交状态：已确认
```

如果结果不确定：

```text
Blackboard 返回的提交结果不确定，插件不会自动重试。
请登录 Blackboard 检查提交状态后再决定是否操作。
```

## 13. 测试设计

### 13.1 Runner 单元测试

至少覆盖：

- 正确传递 `SUSTECH_MASTER_PASSWORD`；
- 正确传递 `SUSTECH_PROFILE`；
- 密码不出现在日志；
- 不使用 `shell=True`；
- JSON stdout 可以解析；
- 非零退出码可以转换为插件错误；
- 子进程超时可以终止；
- 缺少主密码时不会启动交互式等待；
- 非法 operation 会被拒绝；
- 下载和导出目标不能逃逸 `download_root`；
- 符号链接不能绕过输出目录检查；
- 默认不覆盖已有文件；
- 超过文件大小限制时会终止下载并删除不完整文件；
- `submit_preview` 可以生成预览和文件哈希；
- `submit_apply` 不能绕过确认 token；
- 文件被修改后确认阶段会拒绝提交；
- 过期或其他用户的确认 token 会被拒绝。

### 13.2 参数测试

至少覆盖：

- `days=0` 被拒绝或修正；
- `days=91` 被拒绝或限制为 90；
- 非法 `submission_state` 被拒绝；
- 非法日期被拒绝；
- `week` 和 `date` 同时出现时被拒绝；
- 超长课程关键词被拒绝；
- 非法课程、内容或附件 ID 被拒绝；
- 任意目标路径、`..` 和路径分隔符被拒绝；
- 输出目录之外的路径被拒绝；
- `input_root` 之外的提交文件被拒绝；
- `content_id` 和 `column_id` 同时出现时被拒绝；
- 作业提交 comment 超出长度限制时被拒绝；
- `allow_late` 只有用户明确设置时才启用。

### 13.3 权限测试

至少覆盖：

- 群聊在 `private_only=true` 时被拒绝；
- 非白名单用户被拒绝；
- 白名单用户可以执行查询；
- 白名单用户可以下载文件；
- 未授权用户不能下载文件或提交作业；
- `allow_file_operations=false` 时文件操作被拒绝；
- `allow_assignment_submission=false` 时作业提交被拒绝；
- 白名单用户可以生成提交预览；
- 没有明确确认时不能执行提交；
- LLM Tool 和显式命令都执行同一套权限检查。

### 13.4 Fake CLI 集成测试

可以创建临时 fake `sustech` 可执行文件，用于模拟以下结果：

```json
{
  "schemaVersion": "1",
  "ok": true,
  "command": "bb deadlines",
  "data": {"deadlines": []}
}
```

还应模拟：

- `bb download` 成功写入受控输出目录；
- `tis ical` 成功写入 `.ics` 文件；
- 下载超过大小限制；
- 输出路径逃逸；
- `bb submit preview` 返回预览和哈希；
- `bb submit apply` 只在带有正确 `--expected-sha256` 和 `--confirm` 时成功；
- 提交结果不确定并返回 `DO_NOT_RETRY_AUTOMATICALLY`。

验证插件不会依赖真实 Blackboard 才能完成 Runner、路径和提交状态机测试。

### 13.5 手动验收

先在服务器上验证：

```bash
which sustech
sustech --version
export SUSTECH_MASTER_PASSWORD='正确的主密码'
sustech auth status --json
sustech bb deadlines --days 14 --json
sustech tis schedule --json
sustech bb download COURSE_ID CONTENT_ID ATTACHMENT_ID \
  --destination ./test-download --json
sustech tis ical --destination ./test-calendar.ics --json
sustech bb submit preview \
  --course-id COURSE_ID \
  --content-id CONTENT_ID \
  --file ./test-input.pdf \
  --json
```

然后在 AstrBot 中测试：

```text
/sustech-status
/sustech-ddl
/sustech-schedule
/sustech-courses
/sustech-download
/sustech-calendar-export
/sustech-submit-preview
```

确认作业提交时必须看到预览中的：

- 课程名称；
- 作业名称；
- 文件名；
- 文件大小；
- SHA-256；
- 是否迟交；
- 短期确认 token。

只有用户明确执行 `/sustech-submit-confirm TOKEN` 后，插件才允许调用 `bb submit apply`。

再测试自然语言：

```text
帮我查未来 7 天有哪些作业要截止
我今天有什么课？
帮我看看这周的课表
查询我的 Blackboard 课程
帮我下载这门课的课件
帮我把课表导出成日历文件
准备提交这份作业
```

测试以下负面场景：

- 未授权用户尝试下载文件；
- 群聊中请求下载个人课程文件；
- 用户要求写入任意绝对路径；
- 用户要求读取 `input_root` 外的作业文件；
- 文件预览后被替换再确认；
- 确认 token 过期；
- CLI 返回不确定提交结果。

## 14. 部署说明

### 14.1 安装 `sustech-cli`

在 `sustech-cli` 源码目录：

```bash
npm install
npm run build
npm install -g .
```

验证：

```bash
which sustech
sustech --version
```

### 14.2 安装插件

```bash
cd /path/to/AstrBot/data/plugins
git clone <plugin-repository-url> astrbot_plugin_sustech_cli
```

或者将插件目录复制到：

```text
AstrBot/data/plugins/astrbot_plugin_sustech_cli/
```

### 14.3 配置插件

在 AstrBot WebUI 中配置：

```text
sustech_command: /usr/local/bin/sustech
master_password: <SUSTech 加密存储主密码>
profile: default
private_only: true
allowed_users: <管理员用户 ID>
enable_llm_tools: true
allow_file_operations: true
download_root: data/sustech-cli/files
input_root: data/sustech-cli/inputs
max_download_bytes: 52428800
allow_assignment_submission: true
confirmation_ttl_seconds: 600
```

如果 AstrBot 是通过 systemd、Docker 或其他服务管理器运行，不能假设它继承当前 Shell 的 PATH。建议在配置中使用 `sustech` 的绝对路径。

### 14.4 重新加载插件

在 AstrBot WebUI 中：

```text
Extensions → Plugins → astrbot_plugin_sustech_cli → Reload
```

或者重启 AstrBot。

## 15. 第一版完成标准

完成第一版后，必须满足：

- [ ] AstrBot 可以正常加载插件；
- [ ] WebUI 可以配置插件；
- [ ] 可以配置 `sustech` 绝对路径；
- [ ] 可以配置主密码；
- [ ] 主密码不会出现在聊天、日志或 LLM Tool 参数中；
- [ ] Runner 使用 `create_subprocess_exec`；
- [ ] Runner 不使用 Shell；
- [ ] 不支持任意命令执行；
- [ ] `/sustech-status` 正常工作；
- [ ] `/sustech-ddl` 正常工作；
- [ ] `/sustech-schedule` 正常工作；
- [ ] `/sustech-courses` 正常工作；
- [ ] `/sustech-download` 正常工作；
- [ ] `/sustech-calendar-export` 正常工作；
- [ ] `/sustech-submit-preview` 正常工作；
- [ ] `/sustech-submit-confirm` 正常工作；
- [ ] `sustech_get_deadlines` 正常工作；
- [ ] `sustech_get_schedule` 正常工作；
- [ ] `sustech_get_courses` 正常工作；
- [ ] `sustech_download_attachment` 正常工作；
- [ ] `sustech_export_calendar` 正常工作；
- [ ] `sustech_prepare_assignment_submission` 只能生成预览；
- [ ] 作业提交必须经过有效 token 和明确确认；
- [ ] 文件下载和导出只能写入 `download_root`；
- [ ] 作业输入文件只能来自 `input_root` 或插件临时目录；
- [ ] 支持文件大小限制和 SHA-256 校验；
- [ ] 支持主密码错误提示；
- [ ] 支持 profile 不存在提示；
- [ ] 支持查询和下载超时；
- [ ] 支持私聊限制；
- [ ] 支持用户白名单；
- [ ] 有 Runner、权限、路径、提交状态机和格式化测试；
- [ ] 能够使用真实 `sustech-cli` 完成手动验收。

## 16. 后续版本方向

第一版稳定后再考虑：

1. Blackboard 作业详情；
2. 考试安排；
3. 课程公告；
4. 成绩查询；
5. 短时间缓存；
6. 每日 DDL 提醒；
7. 订阅某门课程的变化；
8. 主密码文件或 Docker Secret 支持；
9. 多个 SUSTech profile；
10. 更细粒度的用户和群聊权限；
11. 受控的认证 MCP 工具。

第一版允许受控的文件下载、iCalendar/报告文件写入和 Blackboard 作业提交，但不允许任意文件读写、任意命令执行、自动选课、成绩修改或未确认的远程写操作。
