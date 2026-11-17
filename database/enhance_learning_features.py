#!/usr/bin/env python3
"""Create reading navigation, literary notes, and FSRS-ready learner tables."""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from extend_schema import extend_schema


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "japanese_learning.sqlite3"
SECTION_SIZE = 25

WORK_GUIDES = {
    "aozora-752": (
        "《坊っちゃん》以一位性格直率的青年教师为叙述者，写他从东京来到四国任教后的冲突与见闻。阅读时可以留意第一人称口语、人物绰号和带讽刺感的评价。",
        "作品写于明治时期，部分词语、称呼和价值判断带有当时社会背景。导读为学习辅助草稿，不替代文学研究注释。",
    ),
    "aozora-773": (
        "《こころ》围绕“我”、先生及先生过去的经历展开，主题涉及孤独、信任、罪疚与时代变化。前半适合观察克制的对话，后半书信体句子较长。",
        "故事处在明治时代结束前后，作品中的家庭制度、求学与人际距离需要结合时代语境理解。导读为学习辅助草稿。",
    ),
    "aozora-789": (
        "《吾輩は猫である》借一只猫的视角观察主人苦沙弥及其友人，以夸张、典故和反讽描写知识分子的日常。阅读时不要只追求逐字翻译，要留意叙述者的调侃语气。",
        "作品包含较多明治时期书面表达、汉语词和文化典故；文学说明仅提供语言入口，复杂典故仍需查阅可靠注释。",
    ),
    "aozora-794": (
        "《三四郎》描写青年三四郎从地方来到东京求学后，对城市、学问与情感关系逐渐产生的新认识。可以重点观察心理描写和景物如何推动人物感受。",
        "作品呈现明治后期东京与大学生活，部分社会称谓和男女交往方式具有时代性。导读为学习辅助草稿。",
    ),
}

LITERARY_RULES = (
    ("wagahai", "吾輩", "第一人称「吾辈」", "带有自负、庄重或戏谑色彩的第一人称，在《吾輩は猫である》中也是叙述者的标志。", "现代日常会话很少这样自称。"),
    ("dearu", "である", "书面判断「である」", "相当于礼貌体「です」，常见于论述、说明和文学叙述。", "语气客观、断定感较强。"),
    ("deatta", "であった", "「である」的过去式", "表示书面体的过去判断，相当于「でした／だった」。", "常用于回忆或叙述既成事实。"),
    ("nu", "ぬ", "文语否定「ぬ」", "在许多文学语境中表示否定，可对应现代日语的「ない」；也可能是助动词「ぬ」的其他形态，需要结合词形判断。", "不要看到「ぬ」就机械翻译，应先确认前接动词。"),
    ("zaru", "ざる", "文语否定连体形「ざる」", "相当于现代日语「ない」的连体用法，常出现在「～ざるを得ない」等结构中。", "书面和固定表达中仍然常见。"),
    ("beshi", "べし", "文语助动词「べし」", "可表达应当、当然、推量或可能，具体意义取决于上下文。", "现代日语常见遗留形式有「べき」「べく」。"),
    ("keri", "けり", "文语过去／咏叹「けり」", "可叙述过去，也可带发现、感叹的语气。", "文学作品和诗歌中较常见。"),
    ("nari", "なり", "文语判断「なり」", "常相当于现代日语「である」，也可能是传闻推定形式，需要结合接续判断。", "不要与现代助词「なり」的列举用法混淆。"),
    ("gotoku", "ごとく", "书面比况「ごとく」", "相当于现代日语「ように／ような」，表示如同某事物一样。", "「ごとき」修饰名词，「ごとく」修饰后项。"),
    ("mono_no", "ものの", "让步「ものの」", "承认前项事实，同时指出后项与预期不一致，相当于“虽然……但是……”。", "比普通「けど」更偏书面。"),
    ("tokoro_ga", "ところが", "转折推进「ところが」", "引出与前文预想不同的事实，常用于叙述情节突然转向。", "重点不只是‘但是’，而是预期落空。"),
    ("shikashi", "しかし", "书面转折「しかし」", "明确改变论述方向，相当于“然而”。", "文学叙述中也可制造停顿和对照。"),
    ("yagate", "やがて", "时间推进「やがて」", "表示经过一段时间后出现某结果，可译为“不久、终于、随后”。", "时间跨度由上下文决定。"),
    ("sunawachi", "すなわち", "换言／归结「すなわち」", "用于解释前项或给出归结，相当于“也就是说、即”。", "常见于论述性较强的句子。"),
    ("mottomo", "もっとも", "补充限制「もっとも」", "先陈述一般判断，再补充例外或限制，相当于“不过、话虽如此”。", "与单纯最高级「最も」需要按写法和语境区分。"),
    ("ikanimo", "いかにも", "强调真实感「いかにも」", "表示确实如此，或外观非常符合某种印象。", "常与「～らしい／そうだ」呼应。"),
    ("nanzo", "なんぞ", "旧式强调／轻视「なんぞ」", "可相当于「など」，有时带轻视、反问或强调语气。", "人物语气往往比字面意义更重要。"),
    ("tamau", "給う", "敬意或旧式表达「給う」", "作为补助动词可对动作主体表示敬意；文学人物语言中也可能带旧式语感。", "需根据读音和接续判断是尊敬还是命令形式。"),
    ("yoroshii", "宜しい", "较郑重的「よい」", "表示可以、合适或认可，现代也使用，但汉字写法会显得较旧或正式。", "对话中可能反映人物身份和距离。"),
    ("tokoro_de", "ところで", "转换话题「ところで」", "暂时离开前文，转入新的问题或话题。", "叙述中常作为对话转折标记。"),
    ("ni_souinai", "に相違ない", "强判断「に相違ない」", "表示说话人确信判断无误，相当于“肯定是……”。", "比「に違いない」更旧式或郑重。"),
    ("nakereba_naranai", "なければならない", "义务表达", "表示若不做便不成立，即“必须……”。", "长句中先找出否定条件，再还原整体义务意义。"),
    ("wake_dearu", "わけである", "归结说明", "根据前文条件得出自然结论，相当于“也就是说／因此便是……”。", "重点是逻辑归结，不只是名词「わけ」。"),
    ("ni_hokanaranai", "にほかならない", "限定性断定", "强调本质或唯一解释，相当于“无非就是／正是”。", "常用于论述结论。"),
)


def main() -> None:
    backup = DATABASE.with_name(
        f"{DATABASE.stem}.before-learning-features-{datetime.now():%Y%m%d-%H%M%S}{DATABASE.suffix}"
    )
    shutil.copy2(DATABASE, backup)
    print(f"Backup: {backup}")
    with sqlite3.connect(DATABASE) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        extend_schema(connection)
        for work_id, (guide, era_note) in WORK_GUIDES.items():
            connection.execute(
                "UPDATE reading_works SET guide_zh=?,guide_status='generated',era_note_zh=? WHERE id=?",
                (guide, era_note, work_id),
            )

        connection.execute("DELETE FROM reading_sections")
        section_count = 0
        for work_id, maximum in connection.execute(
            "SELECT work_id,MAX(position) FROM reading_sentences GROUP BY work_id"
        ):
            for number in range(1, math.ceil(maximum / SECTION_SIZE) + 1):
                start = (number - 1) * SECTION_SIZE + 1
                end = min(number * SECTION_SIZE, maximum)
                connection.execute(
                    """INSERT INTO reading_sections
                       (id,work_id,section_number,title_zh,start_position,end_position,summary_zh,is_generated)
                       VALUES (?,?,?,?,?,?,?,1)""",
                    (
                        f"{work_id}-section-{number:04d}",
                        work_id,
                        number,
                        f"连续阅读 {number}",
                        start,
                        end,
                        f"按青空原文顺序整理的第{start}至{end}句；这是学习用连续单元，不冒充原作章节标题。",
                    ),
                )
                section_count += 1

        connection.executemany(
            """INSERT INTO literary_expression_rules
               (id,pattern,title_zh,explanation_zh,nuance_zh,status)
               VALUES (?,?,?,?,?,'generated')
               ON CONFLICT(id) DO UPDATE SET pattern=excluded.pattern,title_zh=excluded.title_zh,
                   explanation_zh=excluded.explanation_zh,nuance_zh=excluded.nuance_zh""",
            LITERARY_RULES,
        )
        connection.execute(
            "INSERT OR IGNORE INTO learner_preferences(learner_id,desired_retention) SELECT id,0.9 FROM learner_profiles"
        )
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('reading_section_size',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SECTION_SIZE),),
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(integrity)
        connection.commit()
        print(json.dumps({
            "reading_sections": section_count,
            "literary_rules": len(LITERARY_RULES),
            "guided_works": connection.execute("SELECT COUNT(*) FROM reading_works WHERE guide_zh IS NOT NULL").fetchone()[0],
            "integrity": integrity,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
