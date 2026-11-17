# 日语句子学习数据库

这是句子课程、原文阅读和本地词典共用的SQLite数据库。当前版本包含：

- 510条核心句；
- 1530条固定变体；
- 3060条生活场景扩展；
- 1020条个性化表达模板；
- 其中原始基础层仍为500 / 1500 / 3000 / 1000，共6000条；
- 另有10个N1语法家族、120条N1起步课程句；
- 14,000条青空文库阅读句（课程与阅读合计20,120句）；
- 219,127个JMdict词条和328,833个词形；
- 217,489个带简体中文义项的词条和12,000条构词记忆线索；
- 13,108个KANJIDIC2汉字条目；
- JmdictFurigana词内注音；
- 课程句及阅读句到词典的逐词索引。

当前版本用于确认以下设计：

- 一个核心句只引入一个主要语法；
- 每个核心句带3个固定变体、6个生活场景扩展和2个个性化模板；
- 语法的标准解释只保存一次；
- 每句话另有针对当前语境的简短解释；
- 每句话都有教学切分、完整读音、中文译文和TTS文本；
- 句子来源和版权状态单独记录；
- 已为 JMdict、KANJIDIC2 和 JmdictFurigana 的后续导入预留表；
- 已为听力、理解和主动表达三种学习进度预留表。

## 文件

- `schema.sql`：数据库结构。
- `generate_full_database.py`：从结构化语法与词汇模板生成6000句并重建数据库。
- `validate_database.py`：检查外键、句子数量、变体数量、语法角色和教学切分。
- `download_dictionary_sources.py`：下载官方词典源文件到被Git忽略的缓存目录。
- `import_dictionary.py`：安全导入词典并建立课程句逐词索引。
- `enrich_chinese_dictionary.py`：导入可追溯的Tomoshi简体中文层并生成待审校构词线索。
- `import_n1_supplement.py`：以幂等方式补入10个N1语法家族和120条课程句。
- `import_aozora_reading.py`：导入带来源的公有领域阅读句。
- `seed_memory_notes.py`：加入少量明确标记为生成内容的基础词记忆联想。
- `enhance_learning_features.py`：生成连续阅读单元、作品导读和统一文学表达规则。
- `japanese_learning.sqlite3`：生成后的数据库文件。

## 重建与检查

```powershell
database\.venv\Scripts\python.exe database\generate_full_database.py
database\.venv\Scripts\python.exe database\import_dictionary.py
database\.venv\Scripts\python.exe database\enrich_chinese_dictionary.py
database\.venv\Scripts\python.exe database\import_n1_supplement.py
database\.venv\Scripts\python.exe database\import_aozora_reading.py
database\.venv\Scripts\python.exe database\enhance_learning_features.py
database\.venv\Scripts\python.exe database\validate_database.py
```

所有批量内容当前标记为 `draft`。审校后再把语法家族或句子提升为 `reviewed` / `published`，不直接手工修改二进制数据库。

词典、N1和阅读扩展不会调用 `generate_full_database.py`；导入前会创建时间戳备份，并在提交前检查6000条基础课程句及SQLite完整性。`generate_full_database.py`只生成基础层，因此完整重建时必须按上面的顺序再次运行N1补充脚本。

## 版权策略

固定课程优先使用原创句子。青空文库内容只从明确标记为著作权已失效的作品中导入，并保留作者、作品、底本和原始链接。以后用户导入影视字幕或其他内容时，保存来源、用途和版权状态；未取得授权的作品内容不打包进公开发行的课程数据库。

## 假名注音

数据库不保存 `<ruby>` 或 `<rt>` HTML。`sentences.text` 保存纯日文，`sentence_ruby_segments` 分别保存原文片段和假名；应用界面负责把这些字段渲染为汉字上方的假名。
