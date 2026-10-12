"""方言无关的词法检索：CJK 感知分词 + BM25 打分（纯 Python，零三方依赖）。

## 为什么手写 BM25 而不是装 `rank_bm25` / 用 PG 的 FTS

1. **方言**：本仓库同时跑 sqlite（现行部署）与 postgresql（#106/#107 迁移就绪）。
   PG 的 `tsvector` 对中文**不开箱可用**（需 `zhparser`/`pg_jieba` 扩展，
   香港这台共置机没装、也不该为了搜索扩装一个会拖垮构建的扩展）；
   把搜索写成 `to_tsquery` 就等于把部署面钉死在 PG + 扩展上。
2. **依赖**：`rank_bm25` 是小库但**未装**（已实测 `find_spec('rank_bm25') is False`），
   引入新依赖要走镜像重建 + 供应链评审；而 BM25 的核心只有一个打分公式。
3. **规模**：语料是**已公开且已完成**的歌曲（不是全部生成品）。
   共置主机 1g/1cpu，规模在万级以内。本机实测（见相似推荐结论）：
   2 万篇文档建索引 0.03s、单次查询 0.05s —— 全量在 Python 进程内算完全够用，
   上倒排索引/向量库属于**没有读者收益的复杂度**。

## 语言切分：中日韩逐字 + 拉丁按词

中文没有空格，`str.split()` 会把整句当一个 token（于是"雨夜"永远匹配不到"雨夜钢琴"）。
两种思路：装分词器（jieba，未装且新依赖）或在**检索层**用 bigram。
这里用**一元 + 二元混合**（unigram + bigram），它不需要词典：
- unigram 保证召回（"钢琴" 能命中），
- bigram 提供区分度（"伤感" 与 "感伤" 的二元组不同，排序才有意义）。
"""

from __future__ import annotations

import math
import re
from collections import Counter

# BM25 标准参数（Okapi）：k1 控制词频饱和，b 控制长度归一。取值用文献默认，不调参。
BM25_K1 = 1.2
BM25_B = 0.75

_LATIN = re.compile(r"[a-zA-Z0-9]+")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def tokenize(text: str | None) -> list[str]:
    """把文本切成检索 token 列表（拉丁小写词 + CJK 一元/二元）。

    ⚠ 拉丁与 CJK 分开处理：`k-pop` / `lo-fi` 这类带连字符的写法必须拆成
    `k`、`pop`（否则 `pop` 搜不到 k-pop），而 CJK 侧不能按空格切。
    """
    if not text:
        return []
    low = text.lower()
    tokens: list[str] = _LATIN.findall(low)
    cjk_runs = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", low)
    for run in cjk_runs:
        tokens.extend(run)  # unigram
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))  # bigram
    return tokens


class Bm25Index:
    """内存 BM25 索引。`docs` 为 {doc_id: 文本}，空索引调用不报错。"""

    def __init__(self, docs: dict[int, str]):
        self._ids: list[int] = []
        self._tf: list[Counter] = []
        self._len: list[int] = []
        self._df: Counter = Counter()
        for doc_id, text in docs.items():
            tokens = tokenize(text)
            tf = Counter(tokens)
            self._ids.append(doc_id)
            self._tf.append(tf)
            self._len.append(len(tokens) or 1)
            for token in tf:
                self._df[token] += 1
        self._n = len(self._ids)
        self._avgdl = (sum(self._len) / self._n) if self._n else 1.0

    @property
    def size(self) -> int:
        return self._n

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        """返回 [(doc_id, score)]，按分数降序，仅含分数 > 0 的文档。"""
        if not self._n or not query:
            return []
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []
        scored: list[tuple[int, float]] = []
        for idx, doc_id in enumerate(self._ids):
            tf = self._tf[idx]
            dl = self._len[idx]
            score = 0.0
            for token in q_tokens:
                f = tf.get(token, 0)
                if not f:
                    continue
                df = self._df.get(token, 0)
                # 概率性 IDF，+0.5 平滑避免 df≈N 时除零/负值
                idf = math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))
                denom = f + BM25_K1 * (1 - BM25_B + BM25_B * dl / self._avgdl)
                score += idf * f * (BM25_K1 + 1) / denom
            if score > 0:
                scored.append((doc_id, score))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:limit]


def song_document(song) -> str:
    """歌曲检索文本的三段拼装。**只有真实存在的列**，不臆造字段。

    权重靠**重复**而不是自定义权重表表达（BM25 里 tf 会饱和，重复 3 次不足以
    让 title 永远压过 lyric，但足以在其它条件相同时给出正确次序）：
    - title ×3：用户在搜索框里输入的多半是歌名；
    - style ×2：风格词是标签浏览的文本后备（闭词表未命中时仍能搜到）；
    - lyric/prompt ×1：宽面召回（这句歌词/主题词）。
    """
    title = getattr(song, "title", "") or ""
    style = getattr(song, "style", "") or ""
    lyric = getattr(song, "lyric", "") or ""
    prompt = getattr(song, "prompt", "") or ""
    return " ".join([title] * 3 + [style] * 2 + [lyric, prompt])
