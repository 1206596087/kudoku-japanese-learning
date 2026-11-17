# 句读 · 本地日语学习应用

“句读”是一款围绕完整句子学习日语的本地Web应用。当前数据库包含6120条结构化课程句（6000条基础课程句与120条N1补充句）、14,000条带作品出处的青空文库阅读句，以及219,127个JMdict词条。学习流程通过盲听、假名注音、语法拆解、换词迁移、原文阅读和间隔复习形成闭环。

## 功能

- 先听声音、后显示日文的学习流程；
- 使用浏览器本机日语TTS，无需真人录音或云端服务；
- 从数据库注音分段安全渲染汉字上方假名；
- 始终显示、点击显示、始终隐藏三种假名模式；
- 每个核心句关联3个固定变体、6个生活场景句和2个个性模板；
- 听力、理解、表达三维学习记录；
- 按到期时间复习，并优先换成同结构的其他句子；
- 听音判断、中文回想、句式识别三种复习题型；
- FSRS 6复习调度，可设置85%、90%或95%目标记忆率；
- 单人私人账号、30天本地会话和密码保护；
- 记录每句出现/完成次数、最近学习时间和核心句单元掌握度；
- 自适应取句与“随机一句”，不会每次从同一个句子开始；
- N5～N1语法入口、句子搜索和类型筛选；
- 21.9万词条本地词典，其中217,489个词条已有可追溯的简体中文机器辅助释义；
- 12,000条自动生成、明确标成待审校的构词记忆线索；
- 句子逐词索引，点击词语即可查看词形、释义、课程例句和原文例句；
- 常用助词在句中解释其连接作用，而不是只显示孤立词义；
- 14,000条青空文库阅读句、561个连续阅读单元、阅读进度、书签和生词收藏；
- 4部作品中文导读草稿及24类统一文学表达说明；
- 任意日文句子的本地形态拆解，提供原形、读音、词性和直译线索；
- 桌面侧栏与手机底部导航；
- 本地保存语音、速度、假名、动画和每日目标设置；
- 双重确认后重置学习进度，不删除课程内容。

## 目录

```text
app/
  main.py                 FastAPI后端和SQLite查询
  static/
    index.html            单页应用结构
    styles.css            桌面与手机响应式样式
    app.js                学习、复习、TTS和设置逻辑
    manifest.webmanifest  PWA基础配置
    service-worker.js     本地静态资源缓存
database/
  japanese_learning.sqlite3
  schema.sql
  download_dictionary_sources.py
  import_dictionary.py
  enrich_chinese_dictionary.py
  import_n1_supplement.py
  import_aozora_reading.py
  enhance_learning_features.py
  seed_memory_notes.py
  validate_database.py
tests/
  test_api.py
requirements.txt
start.bat                 仅本机访问
start_lan.bat             同一局域网手机访问
THIRD_PARTY_DATA.md        第三方数据署名与发布注意事项
```

## Windows启动

双击 `start.bat`，或者在PowerShell中执行：

```powershell
.\start.bat
```

第一次启动会自动创建 `.venv` 并安装依赖。浏览器打开：

```text
http://127.0.0.1:8000
```

首次进入会要求创建唯一的私人账号。用户名至少3个字符，密码至少8位；密码仅保存PBKDF2哈希，不保存明文。登录会话默认保持30天。若以后通过Cloudflare HTTPS发布，请设置环境变量 `NIHONGO_COOKIE_SECURE=1`。

停止服务时，在运行窗口中按 `Ctrl+C`。

## 手机访问

1. 确保手机和电脑连接同一个Wi-Fi。
2. 双击 `start_lan.bat`。
3. 在PowerShell运行 `ipconfig`，找到电脑的IPv4地址，例如 `192.168.1.23`。
4. 手机浏览器访问 `http://192.168.1.23:8000`。
5. 如果Windows防火墙询问，只允许“专用网络”访问即可。

语音由手机或电脑浏览器调用系统日语语音。若没有语音，请在系统语言/辅助功能设置中安装日语语音包。手机浏览器要求用户点击后才允许播放，这是浏览器的正常安全限制。

## 数据库使用

应用读取：

- `sentences`：正文、中文、场景、TTS文本和类型；
- `sentence_ruby_segments`：原文片段与假名，不含HTML；
- `sentence_chunks`：教学意义切分；
- `grammar_points` / `sentence_grammar`：语法及句子关系；
- `learner_sentence_state`：听力、理解、表达和复习到期时间；
- `learner_grammar_state`：语法掌握度。
- `learner_sentence_exposure`：句子出现次数、完成次数及最近时间；
- `learner_unit_state`：核心句及其扩展句组成的单元掌握度；
- `app_accounts` / `app_sessions`：单人私人账号和登录会话；
- `lexemes` / `lexeme_forms` / `lexeme_glosses`：JMdict词条、词形和释义；
- `furigana_segments`：JmdictFurigana词内注音；
- `sentence_lexemes`：6120条课程句的逐词索引；
- `reading_works` / `reading_sentences`：青空作品与14,000条阅读句；
- `reading_sentence_lexemes`：阅读原文到词典词条的索引；
- `kanji_entries` / `kanji_readings`：KANJIDIC2汉字信息。

应用首次启动会增加 `app_learning_events`，仅用于统计今日学习量和最近语法。它不会修改课程内容。设置保存在浏览器 `localStorage`。

数据库课程内容目前为自动生成的 `draft`，结构完整，但仍可能存在少量语法正确而场景搭配不够自然的句子。基础课程覆盖N5～N2；N1已有10个语法家族、120条可完整学习和复习的起步内容，但尚不是完整N1课程。青空阅读句保留原文，不伪造中文翻译；用户可先用逐词索引和助词解释理解结构。

## 规模依据

国立国语研究所相关研究资料给出的估算是：日语达到约95%的文本词汇覆盖通常需要约9,000～12,000个词元，达到98%约需20,000～24,000个词元。因此本项目区分两层：学习路径优先安排高频约12,000词和结构化课程句；完整JMdict负责低频词、专门词和阅读查词。20,000句是可继续扩展的第一阶段语料规模，不等于学完即可自动达到母语水平。

## 重建扩展数据

以下操作会重建词典索引，执行前会自动生成带时间戳的数据库备份：

```powershell
.\.venv\Scripts\python.exe database\download_dictionary_sources.py
.\.venv\Scripts\python.exe database\import_dictionary.py
.\.venv\Scripts\python.exe database\enrich_chinese_dictionary.py
.\.venv\Scripts\python.exe database\import_n1_supplement.py
.\.venv\Scripts\python.exe database\import_aozora_reading.py
.\.venv\Scripts\python.exe database\enhance_learning_features.py
.\.venv\Scripts\python.exe database\seed_memory_notes.py
```

第三方署名、许可和GitHub发布注意事项见 `THIRD_PARTY_DATA.md`。数据库文件约200MB，超过GitHub普通单文件100MB限制，应使用Release附件、Git LFS，或仅提交可重复构建的导入脚本。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
database\.venv\Scripts\python.exe database\validate_database.py
```

API测试会复制数据库到临时目录，不会污染正式学习进度。

当前真实限制：中文词义与构词记忆线索是机器辅助内容，界面会标记“待审校”，不能冒充人工词典；N1目前仍只有10个起步语法家族；青空导读和文学提示是学习辅助草稿，尚无逐句人工译文；结构拆解已支持分句、连接词和日语顺序直译线索，但汉译日与深层语义消歧仍留待后续实现。私人账号目前是单账号设计，适合个人部署，不是多租户系统。

