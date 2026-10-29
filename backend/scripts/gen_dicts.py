"""从知识库语料抽取领域词典候选。

用法（backend 目录下）：
    .venv/Scripts/python.exe -m scripts.gen_dicts

产出到 app/config/*.txt。生成后需要人工过一遍，尤其是方名。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "app" / "config"
KB = ROOT / "kb_src" / "nihaixia"

FORMULA_RE = re.compile(r"[\u4e00-\u9fa5]{2,8}(?:汤|散|丸|膏|丹|饮|煎)")
HERB_BOLD_RE = re.compile(r"^\*\*([\u4e00-\u9fa5]{1,6})\*\*\s*$", re.MULTILINE)
ACUPOINT_RE = re.compile(r"[\u4e00-\u9fa5]{2,3}(?:穴|俞|募)")

# 方名黑名单：句子片段、煎服法、剂量词
FORMULA_BAD_PREFIX = (
    "不要", "不用", "不是", "也是", "还是", "就是", "仍有", "于是", "再给", "再", "又",
    "会", "与", "以", "为", "上", "下", "主", "治", "有", "无", "不", "未", "该", "此",
    "其", "及", "和", "或", "这", "那", "个", "碗", "一", "两", "三", "四", "五", "六",
    "七", "八", "九", "十", "就", "都", "给", "用", "加", "减", "去", "仍", "先", "后",
    "如", "若", "则", "可", "能", "要", "想", "说", "讲", "开", "吃", "喝", "这是", "那是",
    "像", "因为", "所以", "但是", "如果", "现在", "时候", "一般", "一定", "必须", "可以",
    "应该", "知道", "看到", "讲到", "说到", "问到", "认为", "觉得", "比较", "类似", "等于",
    "属于", "称为", "叫作", "还有", "只有", "没有", "所有", "任何", "其他", "另外",
)
FORMULA_BAD_CONTAINS = ("味", "煎", "服", "煮", "杵", "碗", "钱", "升", "末", "汤主", "方法")

# 标准经穴名表（十四经 + 常用经外奇穴）。
# 穴位名是固定知识，正则抽取会把"下针的穴""与天突"这类碎片吃进来，不值得。
ACUPOINT_SEED = """
中府 云门 尺泽 孔最 列缺 经渠 太渊 鱼际 少商
商阳 二间 三间 合谷 阳溪 偏历 温溜 手三里 曲池 肘髎 臂臑 肩髃 巨骨 天鼎 扶突 迎香
承泣 四白 巨髎 地仓 大迎 颊车 下关 头维 人迎 缺盆 乳根 不容 梁门 天枢 外陵 大巨
水道 归来 气冲 伏兔 阴市 梁丘 犊鼻 足三里 上巨虚 条口 下巨虚 丰隆 解溪 冲阳 陷谷 内庭 厉兑
隐白 大都 太白 公孙 商丘 三阴交 漏谷 地机 阴陵泉 血海 箕门 冲门 大横 腹哀 大包
极泉 青灵 少海 灵道 通里 阴郄 神门 少府 少冲
少泽 前谷 后溪 腕骨 阳谷 养老 支正 小海 肩贞 臑俞 天宗 秉风 曲垣 肩外俞 肩中俞 天窗 天容 颧髎 听宫
睛明 攒竹 眉冲 曲差 五处 承光 通天 络却 玉枕 天柱 大杼 风门 肺俞 厥阴俞 心俞 督俞
膈俞 肝俞 胆俞 脾俞 胃俞 三焦俞 肾俞 气海俞 大肠俞 关元俞 小肠俞 膀胱俞 中膂俞 白环俞
上髎 次髎 中髎 下髎 会阳 承扶 殷门 委阳 委中 附分 魄户 膏肓 神堂 膈关 魂门 阳纲
意舍 胃仓 肓门 志室 秩边 合阳 承筋 承山 飞扬 跗阳 昆仑 仆参 申脉 金门 京骨 束骨 至阴
涌泉 然谷 太溪 大钟 水泉 照海 复溜 交信 筑宾 阴谷 横骨 大赫 气穴 四满 中注 肓俞
商曲 石关 阴都 幽门 步廊 神封 灵墟 神藏 俞府
天池 天泉 曲泽 郄门 间使 内关 大陵 劳宫 中冲
关冲 液门 中渚 阳池 外关 支沟 会宗 三阳络 四渎 天井 清冷渊 消泺 臑会 肩髎 天髎 天牖
翳风 瘈脉 颅息 角孙 耳门 丝竹空
瞳子髎 听会 上关 颔厌 悬颅 曲鬓 率谷 天冲 浮白 完骨 本神 阳白 头临泣 目窗 正营
承灵 脑空 风池 肩井 渊腋 辄筋 日月 京门 带脉 五枢 维道 居髎 环跳 风市 中渎 膝阳关
阳陵泉 阳交 外丘 光明 阳辅 悬钟 丘墟 足临泣 地五会 侠溪 足窍阴
大敦 行间 太冲 中封 蠡沟 中都 膝关 曲泉 阴包 足五里 阴廉 急脉 章门 期门
长强 腰俞 腰阳关 命门 悬枢 脊中 中枢 筋缩 至阳 灵台 神道 身柱 陶道 大椎 哑门 风府
脑户 强间 后顶 百会 前顶 囟会 上星 神庭 素髎 水沟 龈交
会阴 曲骨 中极 关元 石门 气海 阴交 神阙 水分 下脘 建里 中脘 上脘 巨阙 鸠尾 中庭
膻中 玉堂 紫宫 华盖 璇玑 天突 廉泉 承浆
太阳 印堂 鱼腰 四神聪 安眠 定喘 夹脊 十宣 八邪 膝眼 胆囊 阑尾 子宫 八髎
"""

# 方名出现的上下文证据：后面紧跟这些词说明是在指方剂
FORMULA_CTX = re.compile(r"^\s*(?:主之|汤主之|方|证|主|治|加减|加|去|倍|与之|服|用|本|是)")

# 高频经方兜底白名单：语料里出现次数少但必须命中的
FORMULA_SEED = """
桂枝汤 麻黄汤 葛根汤 桂枝加葛根汤 大青龙汤 小青龙汤 麻杏石甘汤 五苓散 茯苓甘草汤
小柴胡汤 大柴胡汤 柴胡桂枝汤 柴胡加龙骨牡蛎汤 四逆散 白虎汤 白虎加人参汤 调胃承气汤
小承气汤 大承气汤 桃核承气汤 抵当汤 栀子豉汤 半夏泻心汤 生姜泻心汤 甘草泻心汤
黄连汤 黄芩汤 干姜黄芩黄连人参汤 理中汤 四逆汤 通脉四逆汤 白通汤 真武汤 附子汤
当归四逆汤 吴茱萸汤 猪苓汤 黄连阿胶汤 栀子干姜汤 麻黄附子细辛汤 麻黄附子甘草汤
乌梅丸 当归四逆加吴茱萸生姜汤 烧裩散 牡蛎泽泻散 竹叶石膏汤 桂枝芍药知母汤
炙甘草汤 麦门冬汤 葶苈大枣泻肺汤 瓜蒌薤白白酒汤 瓜蒌薤白半夏汤 苓桂术甘汤
肾气丸 薯蓣丸 酸枣仁汤 甘麦大枣汤 温经汤 胶艾汤 当归芍药散 桂枝茯苓丸 大黄牡丹汤
薏苡附子败酱散 白头翁汤 桃花汤 赤石脂禹余粮汤 甘草干姜汤 芍药甘草汤 芍药甘草附子汤
茵陈蒿汤 栀子柏皮汤 麻黄连轺赤小豆汤 小半夏汤 小半夏加茯苓汤 半夏厚朴汤 旋覆代赭汤
橘皮竹茹汤 厚朴生姜半夏甘草人参汤 桂枝人参汤 葛根黄芩黄连汤 干姜附子汤 茯苓四逆汤
桂枝甘草汤 桂枝甘草龙骨牡蛎汤 桂枝去芍药汤 桂枝加附子汤 桂枝加厚朴杏子汤 桂枝新加汤
麻黄升麻汤 升麻鳖甲汤 木防己汤 己椒苈黄丸 大黄附子汤 硝石矾石散 猪膏发煎
桂枝加桂汤 茯苓桂枝甘草大枣汤 茯苓桂枝五味甘草汤 泽泻汤 苦酒汤 半夏散 猪肤汤
桔梗汤 甘草汤 文蛤散 三物白散 大陷胸汤 大陷胸丸 小陷胸汤 十枣汤 皂荚丸
厚朴七物汤 厚朴三物汤 大黄黄连泻心汤 附子泻心汤 半夏干姜散 橘皮汤 紫参汤
诃梨勒散 越婢汤 越婢加术汤 防己黄芪汤 防己茯苓汤 黄芪桂枝五物汤 桂枝加黄芪汤
薯蓣丸 大黄蟅虫丸 矾石丸 红蓝花酒 当归生姜羊肉汤 百合地黄汤 甘草泻心汤
射干麻黄汤 厚朴麻黄汤 泽漆汤 桂枝生姜枳实汤 橘枳姜汤 九痛丸 走马汤
"""

# 常见中医症状词表（领域常识，人工整理，语料自动抽取质量不够）
SYMPTOM_SEED = """
恶寒 恶风 发热 潮热 壮热 往来寒热 汗出 无汗 自汗 盗汗 大汗 手足汗出
头痛 头重 眩晕 头晕 目眩 耳鸣 耳聋 目赤 目痛 目干 视物模糊 鼻塞 流涕 喷嚏 咽痛 咽干 喉痛 口苦 口干 口渴 口臭 口渴喜冷 不欲饮
咳嗽 气喘 短气 少气 胸闷 胸痛 心悸 心烦 烦躁 失眠 多梦 嗜睡 健忘 神昏 谵语 抽搐 痉挛 麻木 震颤
恶心 呕吐 干呕 反酸 嗳气 呃逆 胃脘痛 胃痛 心下痞 腹胀 腹痛 胁痛 腰痛 身痛 骨节疼痛 关节疼痛 四肢沉重 四肢冰冷 手足厥冷 厥逆 腰酸 腿软
下利 腹泻 便秘 大便溏 大便干 便血 里急后重 小便不利 小便频数 尿血 遗尿 尿痛 水肿 腹水 黄疸 消瘦 肥胖 倦怠 乏力 疲乏 食欲不振 胃口差 纳差 纳呆
面色苍白 面色萎黄 面赤 面青 面黑 唇淡 瘀斑 皮疹 瘙痒 疮疡 肿块 肿瘤 痰多 痰黄 痰白 干咳 咳血 鼻血 牙龈出血 吐血
舌淡 舌红 舌绛 舌紫 舌胖 舌瘦 齿痕 苔白 苔黄 苔腻 苔薄 苔厚 少苔 无苔 舌干 舌润
脉浮 脉沉 脉迟 脉数 脉弦 脉滑 脉细 脉微 脉弱 脉紧 脉促 脉结代 脉洪 脉虚
月经不调 痛经 经闭 崩漏 带下 乳痛 不孕 阳痿 遗精
畏寒 肢冷 上热下寒 阴实 阳虚 阴虚 气虚 血虚 痰湿 湿热 寒湿 瘀血
"""

STOPWORDS = """
的 了 是 在 有 和 与 或 不 为 以 之 就 都 而 及 也 这 那 一个 一样 什么 怎么 如何 因为 所以 但是 如果
我们 你们 他们 自己 这个 那个 这些 那些 这样 那样 可以 能够 应该 需要 这样 那样 时候 地方 东西
一个 一些 很多 非常 特别 已经 还是 只是 就是 不是 没有 会有 有的 的话 那么 这里 那里
啊 吧 呢 吗 呀 哦 嗯 嘛 啦
"""


def iter_kb_files() -> list[Path]:
    files: list[Path] = []
    for sub in ("modules", "cases", "references/distilled"):
        d = KB / sub
        if not d.exists():
            continue
        files.extend(sorted(d.rglob("*.md")))
    for name in ("SKILL.md", "expression_style.md", "distilled_cases.md"):
        p = KB / name
        if p.exists():
            files.append(p)
    return files


def gen_formulas(texts: list[str]) -> list[str]:
    """方名抽取：种子白名单兜底 + 语料高频且通过上下文验证的候选。

    单纯退火正则会产生"不要开桂枝汤""以白饮"这类碎片，必须用上下文证据二次过滤。
    """
    counter: Counter[str] = Counter()
    ctx_hit: Counter[str] = Counter()

    for t in texts:
        for m in FORMULA_RE.finditer(t):
            w = m.group(0)
            if len(w) < 3 or len(w) > 8:
                continue
            if any(w.startswith(p) for p in FORMULA_BAD_PREFIX):
                continue
            if any(p in w for p in FORMULA_BAD_CONTAINS):
                continue
            counter[w] += 1
            tail = t[m.end() : m.end() + 6]
            if FORMULA_CTX.search(tail):
                ctx_hit[w] += 1

    picked = {
        w
        for w, c in counter.items()
        # 频次够高，且至少 25% 的出场带方剂上下文
        if c >= 4 and ctx_hit[w] / c >= 0.25
    }
    seeded = {w.strip() for w in FORMULA_SEED.split() if w.strip()}
    return sorted(picked | seeded)


def gen_herbs(texts: list[str]) -> list[str]:
    counter: Counter[str] = Counter()
    for t in texts:
        for m in HERB_BOLD_RE.finditer(t):
            w = m.group(1).strip()
            if 1 <= len(w) <= 6 and not any(p in w for p in ("法", "义", "论", "篇", "证", "病")):
                counter[w] += 1
    return sorted({w for w, c in counter.items() if c >= 1})


# 穴位名里的干扰词
ACU_BAD_CHARS = set("个么些样般法量处点种条块部位")
ACU_BAD_WORDS = {
    "什么穴", "这个穴", "那个穴", "一个穴", "几个穴", "这些穴", "那些穴", "一般穴",
    "一下穴", "很多穴", "所有穴", "其他穴", "上面穴", "下面穴", "原穴", "络穴", "募穴",
    "俞穴", "郤穴", "经穴", "合穴", "井穴", "荥穴", "输穴", "八脉交会穴",
}


def gen_acupoints(texts: list[str]) -> list[str]:
    """穴位用标准名表，不用语料正则。仅当标准名在语料中出现过才保留（避免收录本库不涉及的穴）。"""
    seed = {w.strip() for w in ACUPOINT_SEED.split() if w.strip()}
    joined = "\n".join(texts)
    present = {w for w in seed if w in joined}
    # 补别名：标准名 + "穴"
    alias = {f"{w}穴" for w in present}
    return sorted(present | alias)


def gen_diseases() -> list[str]:
    """从医案表的诊断列抽病名。"""
    table = KB / "cases" / "00_merged_table.md"
    if not table.exists():
        return []
    text = table.read_text(encoding="utf-8", errors="ignore")
    lines = [ln for ln in text.split("\n") if ln.strip().startswith("|")][2:]
    counter: Counter[str] = Counter()
    for ln in lines:
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))]
        if len(cells) < 4:
            continue
        diag = cells[3]
        if not diag or diag in ("---", "同上"):
            continue
        # 一行可能有多个病名，按顿号/逗号拆
        for part in re.split(r"[、，,;；/]", diag):
            p = re.sub(r"[（(].*?[）)]", "", part).strip()
            if 1 < len(p) <= 10:
                counter[p] += 1
    out = {w for w, c in counter.items() if c >= 2}
    # 补充常见病名
    out |= {
        "感冒", "失眠", "肺癌", "肝癌", "乳癌", "胃癌", "大肠癌", "血癌", "脑瘤", "淋巴癌",
        "肝硬化", "肝炎", "尿毒症", "肾衰竭", "糖尿病", "高血压", "心脏病", "中风", "红斑狼疮",
        "类风湿", "痛风", "鼻炎", "哮喘", "胃炎", "胃溃疡", "便秘", "腹泻", "眩晕", "头痛",
        "痛经", "不孕", "阳痿", "湿疹", "带状疱疹", "抑郁症", "焦虑症", "自闭症", "帕金森",
    }
    return sorted(out)


def write_dict(path: Path, words: list[str], header: str) -> None:
    lines = [f"# {header}", f"# 自动抽取 {len(words)} 条，需人工复核"]
    lines.extend(words)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  {path.name}: {len(words)}")


def main() -> None:
    if not KB.exists():
        print(f"知识库目录不存在: {KB}")
        sys.exit(1)

    CONFIG.mkdir(parents=True, exist_ok=True)
    files = iter_kb_files()
    print(f"扫描 {len(files)} 个文件…")
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in files]
    joined = "\n".join(texts)

    print("生成词典：")
    write_dict(CONFIG / "formula_dict.txt", gen_formulas(texts), "方剂名词典")
    write_dict(CONFIG / "herb_dict.txt", gen_herbs(texts), "药材名词典")
    write_dict(CONFIG / "acupoint_dict.txt", gen_acupoints(texts), "穴位名词典")
    write_dict(CONFIG / "disease_dict.txt", gen_diseases(), "病名词典")
    write_dict(
        CONFIG / "symptom_dict.txt",
        sorted({w for w in SYMPTOM_SEED.split() if w.strip()}),
        "症状词典",
    )
    write_dict(
        CONFIG / "stopwords.txt",
        sorted({w for w in STOPWORDS.split() if w.strip()}),
        "停用词",
    )

    print(f"\n语料总字数：{len(joined):,}")


if __name__ == "__main__":
    main()
