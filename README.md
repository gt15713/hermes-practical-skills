# Hermes Practical Skills

两份用于真实任务的中文 Agent Skills：认真记录个人笔记，以及按范围采集静态文档站。适合 Hermes，也可在支持 `SKILL.md` 的宿主中按各自安装方式使用。

Two focused Chinese-language skills for careful personal-note editing and bounded static-site collection. No hosted service or paid API is required by either skill.

| Skill | 解决的问题 | 示例请求 |
|---|---|---|
| [personal-notes](skills/personal-notes/SKILL.md) | 记录一句话时保留原文、适量注解、定向读取，写后回读并避免重复追加 | “把这句话记到我指定的笔记，保留原话，加适量注解；关联只选最贴切的。” |
| [site-crawler](skills/site-crawler/SKILL.md) | 把指定静态文档站保存成Markdown素材，限制请求和重定向范围，保护已有文件 | “采集这个文档站的指定栏目，先抓5页，最多15次请求，保留来源，不覆盖已有内容。” |

## 安装到Hermes

先查看内容，再按需安装单个Skill。以下使用Hermes官方文档中的GitHub `owner/repo/path` 来源形式：

```sh
hermes skills inspect gt15713/hermes-practical-skills/skills/personal-notes
hermes skills install gt15713/hermes-practical-skills/skills/personal-notes

hermes skills inspect gt15713/hermes-practical-skills/skills/site-crawler
hermes skills install gt15713/hermes-practical-skills/skills/site-crawler
```

已有同名技能时先比较差异，保留自己的配置与修改；不要强制覆盖。安装仍受宿主的扫描、来源和写入批准规则约束。本仓库是GitHub社区发布，不表示已经被官方市场收录。

其他宿主可把单个完整Skill目录放到其用户技能目录。不要只复制SKILL.md而漏掉参考或脚本；具体目录与重新加载方式以宿主文档为准。

## personal-notes怎么用

首次使用要给出准确目标笔记，随后沿用已确认路径、日期结构与元数据要求。默认是“原话＋适量注解”；用户要求只记原话或深入分析时，以该请求为准。原话、引用、AI解释和整理表达应分清，不把笔记内容一概认作原创或亲身经历。

它使用宿主已有文件工具，没有独立Python依赖。支持普通追加、指定来源注解、修订对应注解；完整文章或视频摄入由已有摄入流程处理。内含的案例为合成文本，不是私人笔记。

## site-crawler怎么用

需要 **Python 3.10+**、`requests`和`trafilatura`。建议在独立虚拟环境安装，避免改变宿主环境。以下从仓库根目录执行：

```sh
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r skills/site-crawler/requirements.txt
.\.venv\Scripts\python.exe skills/site-crawler/scripts/crawl_site.py --url https://docs.example.com/ --out .out/example-docs --max-pages 5 --max-requests 15 --delay 0.5
```

macOS / Linux：

```sh
.venv/bin/python -m pip install -r skills/site-crawler/requirements.txt
.venv/bin/python skills/site-crawler/scripts/crawl_site.py --url https://docs.example.com/ --out .out/example-docs --max-pages 5 --max-requests 15 --delay 0.5
```

将示例URL换成获准采集的真实站点。安装为Skill后，将命令中的脚本和requirements路径替换为实际安装位置。

输出保留 `<!-- source: URL -->`，按站点路径组织为Markdown。默认限制同一主机，子域须显式开启；每次失败请求和重定向跳转都占请求预算。已有文件保留，新结果使用不同文件名；它不是原地增量同步器。

| 退出码 | 含义 |
|---|---|
| 0 | 队列走完或达到成功页上限，未记录错误；不保证已经抓完整个站点 |
| 1 | 请求或写入出现错误，可能保留部分结果 |
| 2 | 请求预算或连续失败阈值触发，或参数校验失败 |

**适用边界**：仅静态HTML，无浏览器JS渲染、登录、代理池或反爬绕过。不会自动解析robots.txt；使用者需核站点规则和授权。URL规范化会去fragment、尾斜杠和内置tracking参数，不能当成逐URL无损镜像。使用可信本地输出目录，不以此工具承诺抵御恶意并发文件系统修改。抓取结果是素材，写入知识库仍按用户明确请求和库规则处理。

## 验证

19项离线行为测试，不需要第三方依赖：

```sh
python skills/site-crawler/tests/test_crawl_site_offline.py -v
```

安装运行依赖后，执行真实HTTP/正文提取测试。测试只监听回环地址，使用合成两页站点，不访问外部网站：

```sh
python skills/site-crawler/tests/test_crawl_site_integration.py -v
```

首次公开版在Windows、Python 3.11.16、requests 2.34.2、trafilatura 2.3.1上验证。隔离测试覆盖query/path冲突、不覆盖旧文件、跨主机重定向、请求预算、失败退出等；集成测试覆盖真实依赖取文、落盘及再次采集时保留旧文件。未宣称所有网站、所有宿主或所有平台均已实测。

personal-notes属于操作指导，格式检查不证明模型未来一定遵守；实际使用仍核对写入结果。仓库不提供性能或心理疗愈效果保证。

## 许可与来源

MIT，保留源包的 `Hermes Agent (GT)` 署名。公开版由GTRATX整理维护；来源和依赖边界见 [NOTICE.md](NOTICE.md)。第三方运行依赖独立安装，不随包重新分发。
