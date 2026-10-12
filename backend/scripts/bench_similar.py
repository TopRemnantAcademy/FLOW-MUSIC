"""相似推荐方案的可行性基准（GAP-014「评估 BM25 vs 向量」）。

为什么要有这个脚本，而不是只在 PR 里贴一段数字：
性能结论会随语料量与依赖变化而**失效**。把测法固化成可执行文件，
下次有人质疑"为什么不上向量"时，`python scripts/bench_similar.py` 就能重新量一遍，
而不是回到评论区考古。

用法（在 backend/ 下）：
    python scripts/bench_similar.py            # 2k / 20k 两档
    python scripts/bench_similar.py 50000      # 自定义规模

⚠ 不引 numpy/faiss：向量一栏刻意用**纯 Python** 余弦，因为它测的正是
「不引入新依赖时向量检索要付多少代价」。引了 faiss 就回到毫秒级，
但那就不是"零依赖"的比较了 —— 比较口径必须写清楚，否则结论会被误读。
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.search.lexical import Bm25Index, tokenize  # noqa: E402

STYLES = [
    "国风 伤感 钢琴",
    "jazz 慵懒 深夜",
    "k-pop 舞曲 女团",
    "EDM 电子 合成器",
    "民谣 木吉他 叙事",
    "christian worship piano",
    "lo-fi hiphop 学习",
    "重金属 摇滚 失真吉他",
    "citypop 复古 日语",
    "trap 说唱 808",
]
LYRIC_WORDS = "雨夜 失恋 月光 心跳 远方 归途 星空 温柔 孤单 重逢 拥抱 岁月".split()
QUERY = "国风 伤感 钢琴"
EMBED_DIM = 256  # 与 model2vec potion-base-8M 同量级（不引模型本身，仅对齐维度做成本比较）


def make_doc(i: int) -> str:
    style = random.choice(STYLES)
    lyric = " ".join(random.choices(LYRIC_WORDS, k=40))
    return f"第{i}首 {style} {style} {lyric} 一个主题词"


def bench(n: int) -> None:
    random.seed(7)  # 固定 seed：两次跑的数字可比
    docs = {i: make_doc(i) for i in range(n)}

    t = time.perf_counter()
    index = Bm25Index(docs)
    build = time.perf_counter() - t
    t = time.perf_counter()
    hits = index.search(QUERY, 10)
    query = time.perf_counter() - t
    print(f"BM25     N={n:<7} build={build:7.3f}s  query={query:7.4f}s  hits={len(hits)}")

    # 纯 Python 哈希嵌入 + 暴力余弦（无 numpy / 无 faiss）
    t = time.perf_counter()
    vectors = []
    for i in range(n):
        v = [0.0] * EMBED_DIM
        for tok in tokenize(docs[i]):
            v[hash(tok) % EMBED_DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        vectors.append([x / norm for x in v])
    embed = time.perf_counter() - t

    q = [0.0] * EMBED_DIM
    for tok in tokenize(QUERY):
        q[hash(tok) % EMBED_DIM] += 1.0
    qn = sum(x * x for x in q) ** 0.5 or 1.0
    q = [x / qn for x in q]

    t = time.perf_counter()
    sims = sorted(
        ((sum(a * b for a, b in zip(q, v)), i) for i, v in enumerate(vectors)), reverse=True
    )[:10]
    brute = time.perf_counter() - t
    print(
        f"向量(纯Python, dim={EMBED_DIM}) N={n:<7} embed={embed:7.3f}s  "
        f"brute_cosine={brute:7.4f}s  hits={len(sims)}"
    )


def show_lexical_gap() -> None:
    """诚实记录词法检索的**真实缺陷**：零共同词时它看不见语义相近的歌。"""
    docs = {1: "伤感的雨夜 钢琴", 2: "失恋的深秋 吉他", 3: "伤感的雨夜 弦乐"}
    index = Bm25Index(docs)
    print("\n[词法检索的收益边界] 查询 = '伤感的雨夜 钢琴'")
    for doc_id, score in index.search("伤感的雨夜 钢琴", 3):
        print(f"  doc{doc_id}: {score:7.3f}  {docs[doc_id]}")
    print("  doc2 语义相近但零共享词 → 近零分。这正是向量检索唯一明确的收益面，")
    print("  也是本阶段判断「不值得为 1g/1cpu 加 30MB 常驻模型」时权衡掉的那部分。")


if __name__ == "__main__":
    sizes = [int(a) for a in sys.argv[1:]] or [2000, 20000]
    for size in sizes:
        bench(size)
    show_lexical_gap()
