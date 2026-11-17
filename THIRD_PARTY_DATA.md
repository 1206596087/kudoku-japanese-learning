# 第三方数据与署名

本项目的程序代码和第三方数据不是同一项授权。发布数据库时必须保留本文件及上游署名。

## JMdict / KANJIDIC2

- 提供者：Electronic Dictionary Research and Development Group（EDRDG）
- 项目主页：https://www.edrdg.org/jmdict/j_jmdict.html
- 许可说明：https://www.edrdg.org/edrdg/licence.html
- 本项目用途：日语词形、读音、英文释义、常用度标记和汉字读音。
- 注意：数据库中的机器推断中文教学释义不是EDRDG原始中文翻译。

## JmdictFurigana

- 项目：https://github.com/Doublevil/JmdictFurigana
- 本项目使用的构建：`2.3.1+2026-09-25`
- 用途：词条内部的汉字—假名对齐。
- 数据按项目说明沿用JMdict的Share-Alike许可；项目程序代码另有MIT许可。

## Tomoshi Dictionary Open Data（简体中文层）

- 项目：https://github.com/tomoshi-app/tomoshi-dict-data
- 本项目使用的发布：`v2026-11-16`
- 用途：按JMdict序号补充简体中文释义，并为KANJIDIC2汉字补充中文义项。
- `zh_defs`、`kanji_gloss`及其派生数据按上游表级声明使用CC BY-SA 4.0，署名为 `JMdict-derived Chinese gloss translations © Y1Z`。
- 上游中文释义为机器辅助内容，并非全部经过系统人工校对；本项目因此将导入义项标成 `is_machine_generated=1`，同时保留JMdict英文原义。
- 本地构词记忆线索由中文义项、词形、读音和汉字义自动组合，状态为 `generated`，不得描述为人工审校内容。

## 青空文库

- 网站：https://www.aozora.gr.jp/
- 使用规范：https://www.aozora.gr.jp/guide/kijyunn.html
- 当前收录作品：夏目漱石《吾輩は猫である》《坊っちゃん》《こころ》《三四郎》的新字新假名版本。
- 每条阅读句通过 `reading_works` 保留作品、作者、作品卡片和原始文件链接。
- 本项目只打包明确标记为公有领域的作品文本；不把“权利人授权免费公开但版权仍存续”的作品自动视为公有领域。

## 发布提醒

SQLite文件目前超过GitHub普通单文件100MB限制。开源代码时可选择：

1. 仓库只保存代码、结构和导入脚本，由使用者自行构建数据库；或
2. 将数据库作为GitHub Release附件发布；或
3. 使用Git LFS，并继续遵守各数据源的署名及Share-Alike要求。

不要把所有数据简单标成“项目原创”，也不要删除词条和原文的来源字段。
