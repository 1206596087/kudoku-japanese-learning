#!/usr/bin/env python3
"""Generate the first 6,000-sentence Japanese learning database.

The generated content is deliberately marked as draft.  It is structurally
complete and suitable for the first app prototype, while later editorial
passes can promote individual grammar families to reviewed/published status.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pykakasi import kakasi


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.sql"
DATABASE_PATH = ROOT / "japanese_learning.sqlite3"
KAKASI = kakasi()
KANJI_RE = re.compile(r"[一-龯々〆ヵヶ]")

EXPECTED_COUNTS = {
    "core": 500,
    "variant": 1500,
    "real": 3000,
    "personal": 1000,
}


@dataclass(frozen=True)
class Subject:
    jp: str
    zh: str


@dataclass(frozen=True)
class Context:
    jp: str
    zh: str


@dataclass(frozen=True)
class VerbPhrase:
    prefix: str
    verb: str
    group: str
    zh: str
    gradable: bool = True
    te_exception: str | None = None
    ta_exception: str | None = None

    def form(self, name: str) -> str:
        return self.prefix + conjugate(
            self.verb,
            self.group,
            name,
            te_exception=self.te_exception,
            ta_exception=self.ta_exception,
        )


@dataclass(frozen=True)
class Clause:
    prefix: str
    verb: str
    group: str
    zh: str
    te_exception: str | None = None
    ta_exception: str | None = None

    def form(self, name: str) -> str:
        return self.prefix + conjugate(
            self.verb,
            self.group,
            name,
            te_exception=self.te_exception,
            ta_exception=self.ta_exception,
        )


@dataclass(frozen=True)
class Nominal:
    jp: str
    zh: str


@dataclass(frozen=True)
class Adjective:
    jp: str
    zh: str
    kind: str


@dataclass(frozen=True)
class Family:
    mode: str
    level: str
    title: str
    pattern: str
    meaning: str
    formation: str
    note: str


@dataclass
class Draft:
    text: str
    translation: str
    chunks: list[tuple[str, str, str]]


I_ROW = {"う": "い", "く": "き", "ぐ": "ぎ", "す": "し", "つ": "ち", "ぬ": "に", "ぶ": "び", "む": "み", "る": "り"}
A_ROW = {"う": "わ", "く": "か", "ぐ": "が", "す": "さ", "つ": "た", "ぬ": "な", "ぶ": "ば", "む": "ま", "る": "ら"}
E_ROW = {"う": "え", "く": "け", "ぐ": "げ", "す": "せ", "つ": "て", "ぬ": "ね", "ぶ": "べ", "む": "め", "る": "れ"}


def conjugate(
    verb: str,
    group: str,
    form: str,
    *,
    te_exception: str | None = None,
    ta_exception: str | None = None,
) -> str:
    if group == "ichidan":
        stem = verb[:-1]
        forms = {
            "dict": verb,
            "stem": stem,
            "masu": stem + "ます",
            "masen": stem + "ません",
            "mashita": stem + "ました",
            "masen_deshita": stem + "ませんでした",
            "nai": stem + "ない",
            "ta": stem + "た",
            "te": stem + "て",
            "potential": stem + "られる",
        }
        return forms[form]

    if group == "suru":
        forms = {
            "dict": "する",
            "stem": "し",
            "masu": "します",
            "masen": "しません",
            "mashita": "しました",
            "masen_deshita": "しませんでした",
            "nai": "しない",
            "ta": "した",
            "te": "して",
            "potential": "できる",
        }
        return forms[form]

    if group == "kuru":
        forms = {
            "dict": "来る",
            "stem": "来",
            "masu": "来ます",
            "masen": "来ません",
            "mashita": "来ました",
            "masen_deshita": "来ませんでした",
            "nai": "来ない",
            "ta": "来た",
            "te": "来て",
            "potential": "来られる",
        }
        return forms[form]

    if group != "godan":
        raise ValueError(f"Unknown verb group: {group}")

    stem, ending = verb[:-1], verb[-1]
    if ending not in I_ROW:
        raise ValueError(f"Unsupported godan ending: {verb}")
    te_map = {"う": "って", "つ": "って", "る": "って", "む": "んで", "ぶ": "んで", "ぬ": "んで", "く": "いて", "ぐ": "いで", "す": "して"}
    ta_map = {"う": "った", "つ": "った", "る": "った", "む": "んだ", "ぶ": "んだ", "ぬ": "んだ", "く": "いた", "ぐ": "いだ", "す": "した"}
    forms = {
        "dict": verb,
        "stem": stem + I_ROW[ending],
        "masu": stem + I_ROW[ending] + "ます",
        "masen": stem + I_ROW[ending] + "ません",
        "mashita": stem + I_ROW[ending] + "ました",
        "masen_deshita": stem + I_ROW[ending] + "ませんでした",
        "nai": stem + A_ROW[ending] + "ない",
        "ta": ta_exception or stem + ta_map[ending],
        "te": te_exception or stem + te_map[ending],
        "potential": stem + E_ROW[ending] + "る",
    }
    return forms[form]


SUBJECTS = [
    Subject("私", "我"), Subject("兄", "哥哥"), Subject("姉", "姐姐"),
    Subject("父", "爸爸"), Subject("母", "妈妈"), Subject("友達", "朋友"),
    Subject("先生", "老师"), Subject("田中さん", "田中先生"),
    Subject("同僚", "同事"), Subject("学生たち", "学生们"),
]
PERSONAL_SUBJECTS = [Subject("私", "我"), Subject("私たち", "我们")]
CONTEXTS = [
    Context("", ""), Context("今日は、", "今天，"), Context("明日は、", "明天，"),
    Context("週末は、", "周末，"), Context("家では、", "在家里，"),
    Context("学校では、", "在学校，"), Context("仕事のあとで、", "下班以后，"),
    Context("時間があるときは、", "有时间的时候，"),
    Context("休みの日は、", "休息日，"), Context("旅行中は、", "旅行期间，"),
    Context("朝は、", "早上，"), Context("夜は、", "晚上，"),
]
HABIT_CONTEXTS = [
    Context("", ""), Context("普段は、", "平时，"), Context("毎朝、", "每天早上，"),
    Context("毎晩、", "每天晚上，"), Context("平日は、", "工作日，"),
    Context("週末は、", "周末，"), Context("休みの日は、", "休息日，"),
    Context("時間があるときは、", "有时间的时候，"), Context("旅行中は、", "旅行期间，"),
    Context("最近は、", "最近，"), Context("春は、", "春天，"), Context("冬は、", "冬天，"),
]
PAST_CONTEXTS = [
    Context("", ""), Context("昨日は、", "昨天，"), Context("先週は、", "上周，"),
    Context("この前、", "前些天，"), Context("旅行中に、", "旅行期间，"),
    Context("仕事のあとで、", "下班以后，"), Context("授業のあとで、", "下课以后，"),
    Context("休みの日に、", "休息日，"), Context("朝、", "早上，"),
    Context("夜、", "晚上，"), Context("去年、", "去年，"),
    Context("学生のころ、", "学生时代，"),
]
FUTURE_CONTEXTS = [
    Context("", ""), Context("明日は、", "明天，"), Context("来週は、", "下周，"),
    Context("このあと、", "接下来，"), Context("週末は、", "周末，"),
    Context("休みの日は、", "休息日，"), Context("仕事のあとで、", "下班以后，"),
    Context("授業のあとで、", "下课以后，"), Context("旅行中は、", "旅行期间，"),
    Context("必要になったら、", "有需要时，"), Context("時間ができたら、", "有时间以后，"),
    Context("来年は、", "明年，"),
]
RULE_CONTEXTS = [
    Context("", ""), Context("ここでは、", "在这里，"), Context("教室では、", "在教室里，"),
    Context("図書館では、", "在图书馆里，"), Context("電車の中では、", "在电车里，"),
    Context("仕事中は、", "工作期间，"), Context("必要なときは、", "需要时，"),
    Context("受付では、", "在服务台，"), Context("出発する前に、", "出发前，"),
    Context("終わったら、", "结束后，"), Context("困ったときは、", "遇到困难时，"),
    Context("確認のために、", "为了确认，"),
]
PROHIBITION_CONTEXTS = [
    Context("ここでは、", "在这里，"), Context("教室では、", "在教室里，"),
    Context("図書館では、", "在图书馆里，"), Context("電車の中では、", "在电车里，"),
    Context("仕事中は、", "工作期间，"), Context("試験中は、", "考试期间，"),
    Context("美術館では、", "在美术馆里，"), Context("病院では、", "在医院里，"),
    Context("運転中は、", "开车时，"), Context("会議中は、", "开会时，"),
    Context("この部屋では、", "在这个房间里，"), Context("受付では、", "在服务台，"),
]
OBLIGATION_CONTEXTS = [
    Context("", ""), Context("今日は、", "今天，"), Context("明日までに、", "明天之前，"),
    Context("仕事のために、", "因为工作需要，"), Context("予定が変わったので、", "因为计划改变了，"),
    Context("時間がないので、", "因为没有时间，"), Context("人手が足りないので、", "因为人手不足，"),
    Context("急いでいるので、", "因为赶时间，"), Context("約束したので、", "因为已经约好了，"),
    Context("確認のために、", "为了确认，"), Context("出発する前に、", "出发前，"),
    Context("必要になったら、", "有需要时，"),
]
EXPERIENCE_CONTEXTS = [
    Context("", ""), Context("以前、", "以前，"), Context("何度か、", "有过几次，"),
    Context("学生のころ、", "学生时代，"), Context("子どものころ、", "小时候，"),
    Context("旅行で、", "旅行时，"), Context("日本に来る前に、", "来日本以前，"),
    Context("仕事で、", "工作中，"), Context("休みの日に、", "休息日，"),
    Context("去年、", "去年，"), Context("海外で、", "在国外，"),
    Context("一度だけ、", "只有一次，"),
]
CLAUSE_CONTEXTS = [
    Context("", ""), Context("明日は、", "明天，"), Context("午後は、", "下午，"),
    Context("来週は、", "下周，"), Context("このあと、", "接下来，"),
    Context("週末は、", "周末，"), Context("今夜は、", "今晚，"),
    Context("来月は、", "下个月，"), Context("予定では、", "按照计划，"),
    Context("今のところ、", "目前，"), Context("天気予報では、", "根据天气预报，"),
    Context("ニュースによると、", "根据新闻，"),
]

ACTIONS = [
    VerbPhrase("本を", "読む", "godan", "读书"),
    VerbPhrase("日本語を勉強", "する", "suru", "学习日语"),
    VerbPhrase("音楽を", "聞く", "godan", "听音乐"),
    VerbPhrase("映画を", "見る", "ichidan", "看电影"),
    VerbPhrase("料理を", "作る", "godan", "做饭"),
    VerbPhrase("写真を", "撮る", "godan", "拍照"),
    VerbPhrase("手紙を", "書く", "godan", "写信"),
    VerbPhrase("部屋を掃除", "する", "suru", "打扫房间"),
    VerbPhrase("買い物を", "する", "suru", "买东西"),
    VerbPhrase("友達に電話", "する", "suru", "给朋友打电话"),
    VerbPhrase("新聞を", "読む", "godan", "读报纸"),
    VerbPhrase("コーヒーを", "飲む", "godan", "喝咖啡"),
    VerbPhrase("朝ご飯を", "食べる", "ichidan", "吃早饭"),
    VerbPhrase("公園を散歩", "する", "suru", "在公园散步"),
    VerbPhrase("電車に", "乗る", "godan", "乘电车"),
    VerbPhrase("会社へ", "行く", "godan", "去公司", te_exception="行って", ta_exception="行った"),
    VerbPhrase("家へ", "帰る", "godan", "回家"),
    VerbPhrase("パソコンを", "使う", "godan", "使用电脑"),
    VerbPhrase("質問に", "答える", "ichidan", "回答问题"),
    VerbPhrase("荷物を", "運ぶ", "godan", "搬运行李"),
    VerbPhrase("早く", "起きる", "ichidan", "早起"),
    VerbPhrase("日本語を", "話す", "godan", "说日语"),
    VerbPhrase("ドアを", "開ける", "ichidan", "开门", gradable=False),
    VerbPhrase("窓を", "閉める", "ichidan", "关窗", gradable=False),
    VerbPhrase("予定を確認", "する", "suru", "确认计划"),
    VerbPhrase("メールを", "送る", "godan", "发送邮件"),
    VerbPhrase("席を予約", "する", "suru", "预订座位"),
    VerbPhrase("運動を", "する", "suru", "运动"),
    VerbPhrase("日記を", "書く", "godan", "写日记"),
    VerbPhrase("財布を", "探す", "godan", "寻找钱包"),
]

CLAUSES = [
    Clause("雨が", "降る", "godan", "会下雨"),
    Clause("電車が", "遅れる", "ichidan", "电车会晚点"),
    Clause("友達が", "来る", "kuru", "朋友会来"),
    Clause("会議が", "始まる", "godan", "会议会开始"),
    Clause("店が", "閉まる", "godan", "商店会关门"),
    Clause("値段が", "上がる", "godan", "价格会上涨"),
    Clause("荷物が", "届く", "godan", "包裹会送到"),
    Clause("仕事が", "終わる", "godan", "工作会结束"),
    Clause("道が", "混む", "godan", "道路会拥堵"),
    Clause("予定が", "変わる", "godan", "计划会改变"),
    Clause("先生が説明", "する", "suru", "老师会说明"),
    Clause("会社が募集", "する", "suru", "公司会招聘"),
    Clause("チームが", "勝つ", "godan", "队伍会获胜"),
    Clause("問題が解決", "する", "suru", "问题会解决"),
    Clause("新しい店が", "できる", "ichidan", "会开一家新店"),
    Clause("予約が", "取れる", "ichidan", "能够预约到"),
    Clause("電車が", "止まる", "godan", "电车会停运"),
    Clause("台風が", "近づく", "godan", "台风会接近"),
    Clause("旅行が中止に", "なる", "godan", "旅行会被取消"),
    Clause("体調がよく", "なる", "godan", "身体会好转"),
]

NOUNS = [
    Nominal("学生", "学生"), Nominal("会社員", "公司职员"), Nominal("先生", "老师"),
    Nominal("留学生", "留学生"), Nominal("店員", "店员"), Nominal("医者", "医生"),
    Nominal("旅行者", "旅行者"), Nominal("責任者", "负责人"), Nominal("初心者", "初学者"),
    Nominal("担当者", "负责人"), Nominal("予定", "计划"), Nominal("問題", "问题"),
]
ADJECTIVES = [
    Adjective("忙しい", "忙", "i"), Adjective("楽しい", "开心", "i"),
    Adjective("難しい", "困难", "i"), Adjective("分かりやすい", "容易理解", "i"),
    Adjective("静か", "安静", "na"), Adjective("便利", "方便", "na"),
    Adjective("安全", "安全", "na"), Adjective("大切", "重要", "na"),
    Adjective("元気", "有精神", "na"), Adjective("親切", "亲切", "na"),
]
PLACES = [
    Nominal("部屋", "房间"), Nominal("机の上", "桌子上"), Nominal("駅の前", "车站前"),
    Nominal("公園", "公园"), Nominal("教室", "教室"), Nominal("台所", "厨房"),
    Nominal("会社", "公司"), Nominal("玄関", "门口"), Nominal("棚の中", "架子里"),
    Nominal("近く", "附近"),
]
HUMAN_PLACES = [
    Nominal("部屋", "房间"), Nominal("駅の前", "车站前"), Nominal("公園", "公园"),
    Nominal("教室", "教室"), Nominal("会社", "公司"), Nominal("玄関", "门口"),
    Nominal("近く", "附近"), Nominal("受付", "服务台"), Nominal("病院", "医院"),
    Nominal("店の前", "商店门口"),
]
OBJECTS = [
    Nominal("本", "书"), Nominal("鍵", "钥匙"), Nominal("傘", "雨伞"), Nominal("椅子", "椅子"),
    Nominal("箱", "箱子"), Nominal("地図", "地图"), Nominal("時計", "钟表"),
    Nominal("自動販売機", "自动售货机"), Nominal("案内板", "指示牌"), Nominal("荷物", "行李"),
]
PEOPLE = [
    Nominal("先生", "老师"), Nominal("学生", "学生"), Nominal("友達", "朋友"),
    Nominal("店員", "店员"), Nominal("子ども", "孩子"), Nominal("旅行者", "游客"),
    Nominal("同僚", "同事"), Nominal("家族", "家人"), Nominal("警察官", "警察"),
    Nominal("医者", "医生"),
]
PERSON_NOUNS = NOUNS[:10]
TOPIC_ACTIONS = [
    Nominal("話します", "谈论"), Nominal("調べます", "调查"), Nominal("説明します", "说明"),
    Nominal("質問します", "提问"), Nominal("考えます", "思考"),
    Nominal("記事を書きます", "写文章"), Nominal("意見を聞きます", "听取意见"),
    Nominal("資料を集めます", "收集资料"), Nominal("発表します", "发表"),
    Nominal("相談します", "商量"),
]
CONDITIONS = [
    Nominal("状況", "情况"), Nominal("人数", "人数"), Nominal("予算", "预算"),
    Nominal("時間", "时间"), Nominal("目的", "目的"), Nominal("年齢", "年龄"),
    Nominal("経験", "经验"), Nominal("季節", "季节"), Nominal("結果", "结果"),
    Nominal("希望", "需求"), Nominal("天気", "天气"), Nominal("場所", "地点"),
]
ADJUSTMENTS = [
    Nominal("方法を変えます", "改变方法"), Nominal("内容を調整します", "调整内容"),
    Nominal("計画を立てます", "制定计划"), Nominal("説明を変えます", "改变说明方式"),
    Nominal("対応を考えます", "考虑应对方式"), Nominal("順番を決めます", "决定顺序"),
    Nominal("時間を調整します", "调整时间"), Nominal("場所を選びます", "选择地点"),
    Nominal("資料を用意します", "准备资料"), Nominal("進め方を決めます", "决定推进方式"),
]


FAMILIES = [
    Family("present", "N5", "礼貌陈述", "～ます", "礼貌地叙述习惯或动作", "动词ます形", "用ます形陈述现在或习惯性的动作。"),
    Family("negative", "N5", "礼貌否定", "～ません", "礼貌地表示不做某事", "动词ます词干＋ません", "把动作变成礼貌的否定。"),
    Family("past", "N5", "礼貌过去", "～ました", "礼貌地表示动作已经发生", "动词ます词干＋ました", "说明动作在过去已经完成。"),
    Family("past_negative", "N5", "礼貌过去否定", "～ませんでした", "表示过去没有做某事", "动词ます词干＋ませんでした", "同时表达礼貌、过去和否定。"),
    Family("question", "N5", "动作疑问", "～ますか", "礼貌询问是否做某事", "动词ます形＋か", "句末的か把陈述变成礼貌疑问。"),
    Family("want", "N5", "表达愿望", "～たいです", "表达自己想做某事", "动词ます词干＋たいです", "たい把动作变成说话人的愿望。"),
    Family("request", "N5", "礼貌请求", "～てください", "请对方做某事", "动词て形＋ください", "用て形连接请求表达。"),
    Family("permission", "N5", "请求许可", "～てもいいですか", "询问是否可以做某事", "动词て形＋もいいですか", "表示即使做该动作也可以吗。"),
    Family("prohibition", "N5", "禁止", "～てはいけません", "表示不可以做某事", "动词て形＋はいけません", "用于规则或明确的禁止。"),
    Family("lets", "N5", "提议一起行动", "～ましょう", "提议一起做某事", "动词ます词干＋ましょう", "说话人主动提出共同活动。"),
    Family("invitation", "N5", "委婉邀请", "～ませんか", "邀请对方一起做某事", "动词ます词干＋ませんか", "否定疑问形式使邀请更委婉。"),
    Family("ongoing", "N5", "正在进行", "～ています", "表示动作正在进行", "动词て形＋います", "把动作描述为正在持续的状态。"),
    Family("experience", "N4", "经历", "～たことがあります", "表示曾经做过某事", "动词た形＋ことがあります", "把过去动作当作一种人生经历。"),
    Family("intention", "N4", "计划和打算", "～つもりです", "表示已经形成的打算", "动词普通形＋つもりです", "用于比临时愿望更明确的计划。"),
    Family("before", "N4", "在做某事之前", "～前に", "表示先后顺序", "动词辞书形＋前に", "前项尚未发生时先做后项。"),
    Family("after", "N4", "做完以后", "～た後で", "表示完成前项后再做后项", "动词た形＋後で", "强调前项完成后的时间顺序。"),
    Family("try", "N4", "尝试", "～てみます", "试着做某事", "动词て形＋みます", "表示为了知道结果而实际尝试。"),
    Family("prepare", "N4", "事先准备", "～ておきます", "预先完成必要动作", "动词て形＋おきます", "表示为了之后的需要提前处理。"),
    Family("complete", "N4", "完成或遗憾", "～てしまいました", "表示动作完成或带有遗憾", "动词て形＋しまいました", "根据语境表达彻底完成或不理想的结果。"),
    Family("must", "N4", "必须", "～なければなりません", "表示必须做某事", "动词ない形去い＋ければなりません", "否定条件与不行组合成必须的含义。"),
    Family("no_need", "N4", "不必", "～なくてもいいです", "表示不做也可以", "动词ない形去い＋くてもいいです", "取消某项动作的必要性。"),
    Family("should", "N4", "建议", "～たほうがいいです", "建议做某事", "动词た形＋ほうがいいです", "比较不同选择后推荐该行动。"),
    Family("too_much", "N4", "过度", "～すぎます", "表示某动作做得过多", "动词ます词干＋すぎます", "指出动作超过合适程度。"),
    Family("while", "N4", "同时进行", "～ながら", "一边做某事一边做另一件事", "动词ます词干＋ながら", "两个动作由同一主体同时进行。"),
    Family("after_doing", "N4", "做完再做", "～てから", "完成前项后进行后项", "动词て形＋から", "明确以前项完成为后项开始的时间点。"),
    Family("list_actions", "N4", "列举动作", "～たり～たりします", "列举若干代表性动作", "动词た形＋り", "表示所列动作只是例子，并非全部。"),
    Family("conditional", "N4", "条件", "～たら", "如果发生前项就进行后项", "动词た形＋ら", "用于假设或某件事完成后的条件。"),
    Family("decide", "N3", "自己决定", "～ことにしました", "表示说话人作出决定", "动词辞书形＋ことにしました", "决定的主体通常是说话人或相关人物。"),
    Family("be_decided", "N3", "被决定", "～ことになりました", "表示事情由安排或环境决定", "动词辞书形＋ことになりました", "弱化个人意志，突出外部决定。"),
    Family("habit", "N3", "有意识地坚持", "～ようにしています", "表示努力保持某种习惯", "动词辞书形＋ようにしています", "强调持续而有意识的实践。"),
    Family("think", "N4", "表达判断", "～と思います", "表达自己的想法或判断", "普通形＋と思います", "と标记思考的具体内容。"),
    Family("hearsay", "N3", "转述听闻", "～そうです", "转述从别处听到的信息", "普通形＋そうです", "这里的そう表示传闻而不是外观。"),
    Family("maybe", "N4", "可能性", "～かもしれません", "表示某事有可能发生", "普通形＋かもしれません", "比と思います保留更多不确定性。"),
    Family("expected", "N3", "有根据的推断", "～はずです", "根据已知信息判断理应如此", "普通形＋はずです", "说话人认为结论有可靠根据。"),
    Family("seems", "N3", "根据迹象推测", "～ようです", "根据观察判断似乎如此", "普通形＋ようです", "强调从当前迹象作出的判断。"),
    Family("apparently", "N3", "貌似或听说", "～らしいです", "表示根据间接信息作判断", "普通形＋らしいです", "语气介于传闻和推测之间。"),
    Family("not_necessarily", "N3", "不一定", "～とは限りません", "否定绝对化判断", "普通形＋とは限りません", "表示存在例外，不能一概而论。"),
    Family("not_exactly", "N3", "并非完全如此", "～わけではありません", "部分否定某种理解", "普通形＋わけではありません", "不是完全否定事实，而是修正过度理解。"),
    Family("no_doubt", "N2", "确信推断", "～に違いありません", "表示说话人确信判断正确", "普通形＋に違いありません", "推断力度很强，但仍属于判断。"),
    Family("reason", "N4", "说明原因", "～ので", "较客观、柔和地说明原因", "普通形＋ので", "比から更适合柔和解释情况。"),
    Family("despite", "N3", "转折", "～のに", "表示结果与预期不一致", "普通形＋のに", "前项通常会让人预期相反的结果。"),
    Family("even_if", "N3", "让步条件", "～ても", "即使前项成立，后项也不改变", "动词て形＋も", "表示条件成立仍不影响结论。"),
    Family("cannot_help", "N2", "不得不", "～ざるを得ません", "表示受情况所迫不得不做", "动词未然形＋ざるを得ません", "强调没有其他现实选择。"),
    Family("not_impossible", "N2", "并非不能", "～ないことはありません", "保留条件地表示可以做到", "动词ない形＋ことはありません", "语气比直接肯定更谨慎。"),
    Family("copula", "N5", "名词判断", "AはBです", "说明人物或事物的身份", "名词＋は＋名词＋です", "は提出主题，です礼貌地作出判断。"),
    Family("comparison", "N5", "比较", "AよりBのほうが～", "比较两者并说明哪一方更符合性质", "AよりBのほうが＋形容词", "より标记比较基准，ほう标记被突出的一方。"),
    Family("exist_object", "N5", "物品存在", "場所に物があります", "说明某处有某物", "地点＋に＋物＋があります", "に表示存在地点，ある用于无生命事物。"),
    Family("exist_person", "N5", "人物存在", "場所に人がいます", "说明某处有人或动物", "地点＋に＋人＋がいます", "いる用于有生命的人或动物。"),
    Family("about", "N3", "关于某主题", "～について", "引出讨论或调查的主题", "名词＋について", "把名词设为说话、思考或调查的对象。"),
    Family("depending", "N2", "根据情况变化", "～に応じて", "根据条件调整做法", "名词＋に応じて", "强调后项随前项条件而变化。"),
]

assert len(FAMILIES) == 50


def reading(text: str) -> str:
    return "".join(item["hira"] for item in KAKASI.convert(text))


def ruby_segments(text: str) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    for item in KAKASI.convert(text):
        surface = item["orig"]
        hira = item["hira"]
        result.append((surface, hira if KANJI_RE.search(surface) else None))
    return result


def context_piece(context: Context) -> list[tuple[str, str, str]]:
    return [(context.jp, context.zh.rstrip("，"), "场景或时间提示")] if context.jp else []


def action_draft(mode: str, context: Context, subject: Subject, a: VerbPhrase, b: VerbPhrase) -> Draft:
    c = context.jp
    cz = context.zh
    topic = f"{subject.jp}は"
    topic_chunk = (topic, subject.zh, "句子主题")
    chunks = context_piece(context)

    if mode == "present":
        body, zh = a.form("masu"), f"{subject.zh}{a.zh}。"
    elif mode == "negative":
        body, zh = a.form("masen"), f"{subject.zh}不{a.zh}。"
    elif mode == "past":
        body, zh = a.form("mashita"), f"{subject.zh}{a.zh}了。"
    elif mode == "past_negative":
        body, zh = a.form("masen_deshita"), f"{subject.zh}没有{a.zh}。"
    elif mode == "question":
        body, zh = a.form("masu") + "か", f"{subject.zh}{a.zh}吗？"
    elif mode == "want":
        body, zh = a.form("stem") + "たいです", f"{subject.zh}想{a.zh}。"
    elif mode == "request":
        topic, topic_chunk = "", None
        body, zh = a.form("te") + "ください", f"请{a.zh}。"
    elif mode == "permission":
        topic, topic_chunk = "", None
        body, zh = a.form("te") + "もいいですか", f"可以{a.zh}吗？"
    elif mode == "prohibition":
        topic, topic_chunk = "", None
        body, zh = a.form("te") + "はいけません", f"不可以{a.zh}。"
    elif mode == "lets":
        topic, topic_chunk = "", None
        body, zh = a.form("stem") + "ましょう", f"一起{a.zh}吧。"
    elif mode == "invitation":
        topic, topic_chunk = "", None
        body, zh = a.form("stem") + "ませんか", f"要不要一起{a.zh}？"
    elif mode == "ongoing":
        body, zh = a.form("te") + "います", f"{subject.zh}正在{a.zh}。"
    elif mode == "experience":
        body, zh = a.form("ta") + "ことがあります", f"{subject.zh}曾经{a.zh}过。"
    elif mode == "intention":
        body, zh = a.form("dict") + "つもりです", f"{subject.zh}打算{a.zh}。"
    elif mode == "before":
        body = a.form("dict") + "前に、" + b.form("masu")
        zh = f"{cz}{subject.zh}在{a.zh}之前，会先{b.zh}。"
    elif mode == "after":
        body = a.form("ta") + "後で、" + b.form("masu")
        zh = f"{cz}{subject.zh}{a.zh}以后，会{b.zh}。"
    elif mode == "try":
        body, zh = a.form("te") + "みます", f"{subject.zh}试着{a.zh}。"
    elif mode == "prepare":
        body, zh = a.form("te") + "おきます", f"{subject.zh}会事先{a.zh}。"
    elif mode == "complete":
        body, zh = a.form("te") + "しまいました", f"{subject.zh}已经把{a.zh}这件事做完了。"
    elif mode == "must":
        body = a.form("nai")[:-1] + "ければなりません"
        zh = f"{subject.zh}必须{a.zh}。"
    elif mode == "no_need":
        body = a.form("nai")[:-1] + "くてもいいです"
        zh = f"{subject.zh}不必{a.zh}。"
    elif mode == "should":
        body, zh = a.form("ta") + "ほうがいいです", f"{subject.zh}最好{a.zh}。"
    elif mode == "too_much":
        body, zh = a.form("stem") + "すぎました", f"{subject.zh}{a.zh}得太多了。"
    elif mode == "while":
        body = a.form("stem") + "ながら、" + b.form("masu")
        zh = f"{subject.zh}一边{a.zh}，一边{b.zh}。"
    elif mode == "after_doing":
        body = a.form("te") + "から、" + b.form("masu")
        zh = f"{subject.zh}{a.zh}以后再{b.zh}。"
    elif mode == "list_actions":
        body = a.form("ta") + "り、" + b.form("ta") + "りします"
        zh = f"{subject.zh}会做{a.zh}、{b.zh}之类的事情。"
    elif mode == "conditional":
        body = a.form("ta") + "ら、" + b.form("masu")
        zh = f"如果{subject.zh}{a.zh}，就会{b.zh}。"
    elif mode == "decide":
        body, zh = a.form("dict") + "ことにしました", f"{subject.zh}决定{a.zh}。"
    elif mode == "be_decided":
        body, zh = a.form("dict") + "ことになりました", f"已经安排{subject.zh}去{a.zh}。"
    elif mode == "habit":
        body, zh = a.form("dict") + "ようにしています", f"{subject.zh}一直尽量坚持{a.zh}。"
    elif mode == "cannot_help":
        # A natural modern equivalent is used for verbs whose classical
        # 未然形 would otherwise require a much larger conjugation table.
        body = a.form("nai")[:-2] + "ざるを得ません" if a.group != "suru" else a.prefix + "せざるを得ません"
        zh = f"{subject.zh}不得不{a.zh}。"
    elif mode == "not_impossible":
        body, zh = a.form("nai") + "ことはありません", f"{subject.zh}也不是不能{a.zh}。"
    else:
        raise ValueError(mode)

    text = c + topic + body + ("？" if body.endswith("か") else "。")
    translation = zh if not cz or zh.startswith(cz) else cz + zh
    if topic_chunk:
        chunks.append(topic_chunk)
    chunks.append((body, a.zh if a == b else f"{a.zh}／{b.zh}", "核心动作和语法结构"))
    return Draft(text, translation, chunks)


def clause_draft(mode: str, context: Context, subject: Subject, clause: Clause, action: VerbPhrase) -> Draft:
    c, cz = context.jp, context.zh
    plain = clause.form("dict")
    if mode == "think":
        body, zh = plain + "と思います", f"我认为{clause.zh}。"
    elif mode == "hearsay":
        body, zh = plain + "そうです", f"听说{clause.zh}。"
    elif mode == "maybe":
        body, zh = plain + "かもしれません", f"可能{clause.zh}。"
    elif mode == "expected":
        body, zh = plain + "はずです", f"按理说{clause.zh}。"
    elif mode == "seems":
        body, zh = plain + "ようです", f"看起来似乎{clause.zh}。"
    elif mode == "apparently":
        body, zh = plain + "らしいです", f"看来{clause.zh}。"
    elif mode == "not_necessarily":
        body, zh = plain + "とは限りません", f"不一定{clause.zh}。"
    elif mode == "not_exactly":
        body, zh = plain + "わけではありません", f"并不是说一定{clause.zh}。"
    elif mode == "no_doubt":
        body, zh = plain + "に違いありません", f"可以肯定，{clause.zh}。"
    elif mode == "reason":
        body = plain + "ので、" + subject.jp + "は" + action.form("masu")
        zh = f"因为{clause.zh}，所以{subject.zh}{action.zh}。"
    elif mode == "despite":
        body = plain + "のに、" + subject.jp + "は" + action.form("masu")
        zh = f"明明{clause.zh}，{subject.zh}却{action.zh}。"
    elif mode == "even_if":
        body = clause.form("te") + "も、" + subject.jp + "は" + action.form("masu")
        zh = f"即使{clause.zh}，{subject.zh}也会{action.zh}。"
    else:
        raise ValueError(mode)
    text = c + body + "。"
    chunks = context_piece(context)
    chunks.extend([
        (plain, clause.zh, "判断或条件内容"),
        (body[len(plain):] if body.startswith(plain) else body, zh.rstrip("。"), "核心语法和结论"),
    ])
    return Draft(text, cz + zh, chunks)


def nominal_draft(mode: str, context: Context, subject: Subject, index: int) -> Draft:
    c, cz = context.jp, context.zh
    noun = PERSON_NOUNS[(index // len(SUBJECTS)) % len(PERSON_NOUNS)] if mode == "copula" else NOUNS[(index // len(SUBJECTS)) % len(NOUNS)]
    adj = ADJECTIVES[index % len(ADJECTIVES)]
    place = (HUMAN_PLACES if mode == "exist_person" else PLACES)[index % len(PLACES)]
    obj = OBJECTS[(index // len(PLACES)) % len(OBJECTS)]
    person = PEOPLE[(index // len(PLACES)) % len(PEOPLE)]
    topic_action = TOPIC_ACTIONS[(index // len(NOUNS)) % len(TOPIC_ACTIONS)]
    condition = CONDITIONS[index % len(CONDITIONS)]
    adjustment = ADJUSTMENTS[(index // len(CONDITIONS)) % len(ADJUSTMENTS)]

    if mode == "copula":
        body = f"{subject.jp}は{noun.jp}です"
        zh = f"{subject.zh}是{noun.zh}。"
        chunks = [(f"{subject.jp}は", subject.zh, "主题"), (f"{noun.jp}です", f"是{noun.zh}", "名词判断")]
    elif mode == "comparison":
        ending = adj.jp + ("です" if adj.kind == "i" else "です")
        body = f"{noun.jp}より{subject.jp}のほうが{ending}"
        zh = f"与{noun.zh}相比，{subject.zh}更{adj.zh}。"
        chunks = [(f"{noun.jp}より", f"与{noun.zh}相比", "比较基准"), (f"{subject.jp}のほうが", subject.zh, "被突出的一方"), (ending, f"更{adj.zh}", "比较结果")]
    elif mode == "exist_object":
        body = f"{place.jp}に{obj.jp}があります"
        zh = f"{place.zh}有{obj.zh}。"
        chunks = [(f"{place.jp}に", place.zh, "存在地点"), (f"{obj.jp}があります", f"有{obj.zh}", "无生命事物的存在")]
    elif mode == "exist_person":
        body = f"{place.jp}に{person.jp}がいます"
        zh = f"{place.zh}有{person.zh}。"
        chunks = [(f"{place.jp}に", place.zh, "存在地点"), (f"{person.jp}がいます", f"有{person.zh}", "人物的存在")]
    elif mode == "about":
        about_noun = NOUNS[index % len(NOUNS)]
        body = f"{about_noun.jp}について{topic_action.jp}"
        zh = f"围绕{about_noun.zh}{topic_action.zh}。"
        chunks = [(f"{about_noun.jp}について", f"关于{about_noun.zh}", "讨论主题"), (topic_action.jp, topic_action.zh, "围绕主题进行的动作")]
    elif mode == "depending":
        body = f"{condition.jp}に応じて、{adjustment.jp}"
        zh = f"会根据{condition.zh}{adjustment.zh}。"
        chunks = [(f"{condition.jp}に応じて", f"根据{condition.zh}", "变化依据"), (adjustment.jp, adjustment.zh, "随条件变化的结果")]
    else:
        raise ValueError(mode)
    return Draft(c + body + "。", cz + zh, context_piece(context) + chunks)


ACTION_MODES = {family.mode for family in FAMILIES[:30]} | {"cannot_help", "not_impossible"}
CLAUSE_MODES = {"think", "hearsay", "maybe", "expected", "seems", "apparently", "not_necessarily", "not_exactly", "no_doubt", "reason", "despite", "even_if"}
NOMINAL_MODES = {"copula", "comparison", "exist_object", "exist_person", "about", "depending"}


def render(family: Family, family_index: int, serial: int, *, personal: bool) -> Draft:
    subjects = PERSONAL_SUBJECTS if personal or family.mode == "want" else SUBJECTS
    actions = [a for a in ACTIONS if a.gradable] if family.mode == "too_much" else ACTIONS

    if family.mode in ACTION_MODES:
        action_index = serial % len(actions)
        subject_index = (serial // len(actions)) % len(subjects)
        context_index = (serial // (len(actions) * len(subjects))) % len(CONTEXTS)
        subject = subjects[subject_index]
        if family.mode == "experience":
            context_pool = EXPERIENCE_CONTEXTS
        elif family.mode in {"past", "past_negative", "complete"}:
            context_pool = PAST_CONTEXTS
        elif family.mode in {"intention", "before", "after", "prepare", "conditional", "decide", "be_decided"}:
            context_pool = FUTURE_CONTEXTS
        elif family.mode == "prohibition":
            context_pool = PROHIBITION_CONTEXTS
        elif family.mode in {"must", "cannot_help"}:
            context_pool = OBLIGATION_CONTEXTS
        elif family.mode in {"request", "permission", "no_need", "should"}:
            context_pool = RULE_CONTEXTS
        else:
            context_pool = HABIT_CONTEXTS
        context = context_pool[(context_index + family_index) % len(context_pool)]
        a = actions[action_index]
        b = ACTIONS[(serial * 7 + family_index * 5 + 1) % len(ACTIONS)]
        if b == a:
            b = ACTIONS[(ACTIONS.index(b) + 1) % len(ACTIONS)]
        return action_draft(family.mode, context, subject, a, b)
    if family.mode in CLAUSE_MODES:
        clause_index = serial % len(CLAUSES)
        context_index = (serial // len(CLAUSES)) % len(CONTEXTS)
        subject_index = (serial // (len(CLAUSES) * len(CONTEXTS))) % len(subjects)
        clause = CLAUSES[clause_index]
        context = CLAUSE_CONTEXTS[(context_index + family_index) % len(CLAUSE_CONTEXTS)]
        subject = subjects[subject_index]
        a = ACTIONS[(serial * 7 + family_index * 3) % len(ACTIONS)]
        return clause_draft(family.mode, context, subject, clause, a)
    if family.mode in NOMINAL_MODES:
        subject_index = serial % len(subjects)
        context_index = (serial // len(subjects)) % len(CONTEXTS)
        subject = subjects[subject_index]
        context = Context("", "")
        if personal:
            context = Context(
                f"{subject.jp}の場合は、",
                f"对于{subject.zh}来说，",
            )
        return nominal_draft(family.mode, context, subject, serial + family_index)
    raise ValueError(f"No renderer for {family.mode}")


def unique_drafts(family: Family, family_index: int, count: int, *, personal: bool, exclude: set[str] | None = None) -> list[Draft]:
    drafts: list[Draft] = []
    seen = set(exclude or set())
    serial = 0
    while len(drafts) < count and serial < 100_000:
        draft = render(family, family_index, serial, personal=personal)
        serial += 1
        if draft.text in seen:
            continue
        seen.add(draft.text)
        drafts.append(draft)
    if len(drafts) != count:
        raise RuntimeError(f"Could not generate {count} unique drafts for {family.mode}: {len(drafts)}")
    return drafts


def insert_sentence(
    con: sqlite3.Connection,
    *,
    sentence_id: str,
    level: str,
    kind: str,
    parent_id: str | None,
    variant_type: str | None,
    family: Family,
    grammar_id: str,
    grammar_role: str,
    draft: Draft,
) -> None:
    full_reading = reading(draft.text)
    con.execute(
        """
        INSERT INTO sentences (
            id, level_code, kind, parent_sentence_id, variant_type,
            text, reading, translation_zh, scene_zh, register,
            grammar_note_zh, audio_text, source_type, source_ref,
            copyright_status, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sentence_id, level, kind, parent_id, variant_type,
            draft.text, full_reading, draft.translation,
            "在日常场景中理解并表达：" + draft.translation,
            "polite",
            f"本句使用「{family.pattern}」。{family.note} 当前句的意思是：{draft.translation}",
            draft.text, "generated", "structured-generator-v1",
            "original", "draft",
        ),
    )
    con.execute(
        "INSERT INTO sentence_grammar (sentence_id, grammar_id, role) VALUES (?, ?, ?)",
        (sentence_id, grammar_id, grammar_role),
    )
    con.executemany(
        """
        INSERT INTO sentence_chunks (
            sentence_id, position, surface, reading, meaning_zh, function_zh
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (sentence_id, pos, surface, reading(surface), meaning, function)
            for pos, (surface, meaning, function) in enumerate(draft.chunks, 1)
            if surface
        ],
    )
    con.executemany(
        """
        INSERT INTO sentence_ruby_segments (
            sentence_id, position, surface, reading
        ) VALUES (?, ?, ?, ?)
        """,
        [
            (sentence_id, pos, surface, ruby_reading)
            for pos, (surface, ruby_reading) in enumerate(ruby_segments(draft.text), 1)
        ],
    )


def generate() -> Path:
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
    con = sqlite3.connect(DATABASE_PATH)
    try:
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("schema_version", "2"),
                ("content_version", "structured-generator-v1"),
                ("built_at_utc", datetime.now(timezone.utc).isoformat()),
                ("content_status", "draft"),
                ("ruby_storage", "plain-text-plus-segments-no-html"),
            ],
        )
        levels = [
            ("ENTRY", 0, "五十音与入门", "掌握声音、假名和最基础表达"),
            ("N5", 1, "N5基础", "理解最常见的日常短句"),
            ("N4", 2, "N4初级", "处理常见生活场景"),
            ("N3", 3, "N3中级", "理解较自然的连续表达"),
            ("N2", 4, "N2中高级", "理解较复杂的意见和说明"),
            ("N1", 5, "N1高级", "理解抽象、正式和细腻表达"),
        ]
        con.executemany("INSERT INTO levels VALUES (?, ?, ?, ?)", levels)
        con.executemany(
            """
            INSERT INTO dictionary_sources (
                id, name, homepage, purpose_zh, license_note_zh, imported_at
            ) VALUES (?, ?, ?, ?, ?, NULL)
            """,
            [
                ("jmdict", "JMdict", "https://www.edrdg.org/jmdict/j_jmdict.html", "单词、读音、词性和释义", "遵循EDRDG许可并定期更新"),
                ("kanjidic2", "KANJIDIC2", "https://www.edrdg.org/wiki/index.php/KANJIDIC_Project", "汉字音读、训读和字形信息", "遵循EDRDG许可并定期更新"),
                ("jmdict_furigana", "JmdictFurigana", "https://github.com/Doublevil/JmdictFurigana", "词内假名对齐", "遵循项目及上游数据许可"),
            ],
        )

        global_core = 0
        for family_index, family in enumerate(FAMILIES, 1):
            grammar_id = f"g{family_index:03d}_{family.mode}"
            con.execute(
                """
                INSERT INTO grammar_points (
                    id, level_code, title_zh, pattern, meaning_zh,
                    formation_zh, rationale_zh, nuance_zh,
                    common_mistakes_zh, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
                """,
                (
                    grammar_id, family.level, family.title, family.pattern,
                    family.meaning, family.formation, family.note,
                    "先理解表达功能，再通过同句式换词迁移。",
                    "不要只背中文翻译，应注意接续形式和使用场景。",
                ),
            )

            general = unique_drafts(family, family_index, 100, personal=False)
            used = {draft.text for draft in general}
            personal = unique_drafts(family, family_index, 20, personal=True, exclude=used)

            for group in range(10):
                global_core += 1
                core_id = f"s{global_core:04d}_core"
                base = group * 10
                insert_sentence(
                    con,
                    sentence_id=core_id,
                    level=family.level,
                    kind="core",
                    parent_id=None,
                    variant_type=None,
                    family=family,
                    grammar_id=grammar_id,
                    grammar_role="new" if group == 0 else "review",
                    draft=general[base],
                )
                for offset in range(1, 4):
                    insert_sentence(
                        con,
                        sentence_id=f"s{global_core:04d}_v{offset}",
                        level=family.level,
                        kind="variant",
                        parent_id=core_id,
                        variant_type=("lexical", "form", "context")[offset - 1],
                        family=family,
                        grammar_id=grammar_id,
                        grammar_role="review",
                        draft=general[base + offset],
                    )
                for offset in range(4, 10):
                    insert_sentence(
                        con,
                        sentence_id=f"s{global_core:04d}_r{offset - 3}",
                        level=family.level,
                        kind="real",
                        parent_id=core_id,
                        variant_type="listening",
                        family=family,
                        grammar_id=grammar_id,
                        grammar_role="review",
                        draft=general[base + offset],
                    )
                for offset in range(2):
                    insert_sentence(
                        con,
                        sentence_id=f"s{global_core:04d}_p{offset + 1}",
                        level=family.level,
                        kind="personal",
                        parent_id=core_id,
                        variant_type="personal",
                        family=family,
                        grammar_id=grammar_id,
                        grammar_role="review",
                        draft=personal[group * 2 + offset],
                    )

        con.execute(
            "INSERT INTO learner_profiles (display_name, current_level_code) VALUES (?, ?)",
            ("本地学习者", "N5"),
        )
        con.commit()
    finally:
        con.close()
    return DATABASE_PATH


if __name__ == "__main__":
    print(generate())
