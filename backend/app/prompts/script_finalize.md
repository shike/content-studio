---
purpose: 脚本终稿产物
version: 1
---
把下面的口播脚本定稿，产出三个部分。只输出 JSON（不要解释、不要代码块标记）：

{
  "teleprompter_text": "提词器版全文：停顿用 / 标记，重音词用 **包裹**，按呼吸感分段",
  "storyboard": [
    {"shot": "镜头/画面说明", "broll_keywords": ["素材库检索关键词"], "subtitle_hint": "字幕卡点提示"}
  ],
  "title_candidates": [
    {"platform": "douyin", "title": "抖音标题（判断/行动指令式）"},
    {"platform": "douyin", "title": "抖音标题第二版（问句式）"},
    {"platform": "channels", "title": "视频号文案"},
    {"platform": "cover", "title": "封面大字（不超过8个字）"}
  ]
}

要求：storyboard 至少 3 个镜头（含口播主镜头与 B-roll 插入点）；title_candidates 至少 4 条，
其中必须含 1 条 platform 为 cover 的封面大字。抖音标题两条按「标题铁律」（2026-10 流量诊断）：
①人群词开头，让对的人 0.5 秒认出"这说的就是我"；②判断/指令式与问句式各一条；≤25 字；
③禁行业黑话缩写（圈外人不懂的词换大白话）；④问句式实测播放翻倍，优先考虑问句。

脚本（JSON）：
