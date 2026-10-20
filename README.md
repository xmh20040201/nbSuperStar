# Spring-Superstar

在线课程学习助手（多平台架构）。

## 已支持的平台

| 平台 | key | 登录方式 | 任务类型 |
|---|---|---|---|
| 超星学习通 | `chaoxing` | 账号密码 / Cookie | 视频、音频、文档、阅读、作业、章节检测、直播、自动签到 |

> 平台列表由代码动态生成，`python main.py --help` 的 `-P/--platform` 参数与
> Web 面板「账号登录 → 学习平台」下拉框会实时反映当前已注册的平台。

## 快速开始

### 命令行

```bash
pip install -r requirements.txt

# 复制配置模板后按需修改
cp config_template.ini config.ini

# 运行（默认超星）
python main.py -c config.ini

# 显式指定平台
python main.py -c config.ini -P chaoxing

# 查看帮助（含当前支持的所有平台）
python main.py --help
```

### Web 面板

```bash
python app.py                 # http://127.0.0.1:5000
python app.py 0.0.0.0 8080    # 指定监听地址与端口
```

在「账号登录」页下拉选择平台后登录。

## 配置

`config.ini` 的 `[common]` 段新增 `platform` 字段：

```ini
[common]
platform = chaoxing   ; 未填写时默认 chaoxing
```

Cookie 按平台分文件存放：

```
cookies/chaoxing.txt
cookies/<platform>.txt
```

> 兼容说明：老版本的根目录 `cookies.txt` 会在首次读取时**自动迁移**到
> `cookies/chaoxing.txt`，原有登录态不会丢失。

## 架构：如何新增一个平台

主流程（`main.py`）只依赖 `api/platform` 的抽象契约，**不含任何平台私有代码**。

```
api/platform/
├── contract.py          # Platform 抽象基类、PlatformMeta、能力枚举、结果枚举
├── registry.py          # 注册表 + 别名 + PlatformManager 工厂
├── account.py           # Account
├── cookies.py           # 按平台的 cookie 持久化
└── adapters/
    ├── __init__.py      # 导入以触发注册
    └── chaoxing.py      # 超星/学习通实现
```

新增平台三步：

```python
# api/platform/adapters/myplatform.py
from api.platform import (
    LoginState, Platform, PlatformCapability, PlatformMeta,
    StudyResult, normalize_course, normalize_point, register_platform,
)

@register_platform
class MyPlatform(Platform):
    key = "myplatform"

    meta = PlatformMeta(
        key="myplatform",
        name="我的平台",
        account_hint="学号",          # 登录表单标签
        capabilities=frozenset({
            PlatformCapability.LOGIN_PASSWORD,
            PlatformCapability.COURSE_LIST,
            PlatformCapability.CHAPTER_LIST,
            PlatformCapability.JOB_LIST,
            PlatformCapability.VIDEO,
        }),
        config_fields=(              # 平台私有登录参数，Web 端自动渲染
            {"key": "school", "label": "学校代码", "type": "text"},
        ),
    )

    def _create_session(self): ...
    def _login_with_password(self) -> LoginState: ...
    def get_course_list(self) -> list[dict]: ...
    def get_course_point(self, course) -> dict: ...
    def get_job_list(self, course, point) -> tuple[list, dict]: ...
    def process_job(self, course, job, job_info, speed=1.0, stop_event=None) -> StudyResult: ...
```

然后在 `api/platform/adapters/__init__.py` 中 `import` 它即可——
`main.py`、Web 路由、前端**都不需要修改**。

### 规范化数据模型

为与历史代码（`api/decode.py`、任务调度）保持兼容，内部统一沿用超星的字段命名，
这是所有平台互通的"普通话"：

```python
course = {"platform", "courseId", "clazzId", "cpi", "title", "teacher"}
point  = {"id", "title", "has_finished", "need_unlock"}
job    = {"type", ...}   # type ∈ video/audio/document/read/work/workid/live
```

平台私有字段可以直接挂在字典上，其它平台会忽略它。
无对应概念时（例如没有 `cpi`）填 `""`。

### 能力降级

平台没实现某类任务时，`main.py` 会记录日志并**跳过该任务点**，
而不是把它判为失败——因此可以"先支持视频，再逐步补作业/直播"。

## 自测

```bash
python tests/test_platform_layer.py    # 抽象层、注册表、cookie 隔离等
python tests/test_run_study_flow.py    # 用一个假平台跑通 run_study 全流程（离线）
```

两个脚本都不需要真实账号，也不会访问网络。
