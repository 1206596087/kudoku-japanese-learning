#!/usr/bin/env python3
"""Seed a small, clearly labelled set of Chinese mnemonic associations."""

from __future__ import annotations

import sqlite3
from pathlib import Path


DATABASE = Path(__file__).resolve().parent / "japanese_learning.sqlite3"
NOTES = {
    "読む": "联想：书里的内容变成可以说出的「言」，一页页读下去。这里是记忆联想，不是字源解释。",
    "聞く": "门里有一只耳朵：把耳朵靠近门，仔细听里面的声音。",
    "話す": "「言」和「舌」一起工作，就是开口说话。",
    "見る": "先用眼睛看见，再慢慢理解；把「みる」和中文的‘觅’作声音联想。",
    "書く": "拿起笔把想法写下来；「かく」也常出现在日记、信件和姓名填写场景。",
    "行く": "从这里向外走就是「行く」；记住它站在说话者当前的位置看方向。",
    "来る": "人或事物朝说话者这里来，所以方向和「行く」相反。",
    "帰る": "一天结束回到自己的归处；中文‘归’与日文意思接近。",
    "食べる": "看到「食」就想到一顿饭，把它和具体餐桌场景绑定。",
    "飲む": "口渴时拿起杯子喝；也能用于‘吃药’，记住日语按吞服动作来表达。",
    "買う": "到店里挑好东西付钱买；发音「かう」可联想‘花钱买’。",
    "学ぶ": "中文‘学’直接提示学习，把它放进‘从经验中学到’的场景。",
    "覚える": "把信息留在感觉和头脑里，就是记住、学会。",
    "忘れる": "事情从心里滑走了，就是忘记；总和「覚える」成对复习。",
    "仕事": "需要完成的‘事’，就是工作；和「仕事をする」整块记忆。",
    "学校": "学习所在的场所；作为地点时常跟「で」，作为去向时常跟「に／へ」。",
    "友達": "能一起到达很多地方的人是朋友；把词作为一个整体读「ともだち」。",
    "時間": "两个时间点之间的间隔；和几点「時」区分开来。",
    "今日": "把‘今天’作为一个整体特殊读作「きょう」，不要逐字拼读。",
    "明日": "日常最常读「あした」，正式语境也会读「あす」；用‘明天见’整句记忆。",
}


def main() -> None:
    updated = 0
    with sqlite3.connect(DATABASE) as connection:
        for written, note in NOTES.items():
            row = connection.execute(
                """SELECT lf.lexeme_id FROM lexeme_forms lf JOIN lexemes l ON l.id=lf.lexeme_id
                   WHERE lf.written_form=? ORDER BY lf.is_common DESC,COALESCE(l.common_rank,999999),lf.id LIMIT 1""",
                (written,),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE lexeme_details SET memory_note_zh=?,memory_note_status='generated' WHERE lexeme_id=?",
                    (note, row[0]),
                )
                updated += 1
    print(f"Seeded {updated} mnemonic notes (status=generated)")


if __name__ == "__main__":
    main()
