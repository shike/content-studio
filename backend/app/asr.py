"""ASR 转写：faster-whisper 封装（懒加载单例，CPU int8）。

首次运行会从 HuggingFace 下载模型（small≈460MB / large-v3≈3GB）；
国内网络慢可在 .env 设置环境后重启，如 export HF_ENDPOINT=https://hf-mirror.com。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from .settings import settings

# 口播领域词表默认值（平台创始租户配置）；租户可在品牌配置 asr_vocab 覆盖
_DEFAULT_INITIAL_PROMPT = "以下是企业经营与行业分享类的中文口播内容。"


def initial_prompt_for(vocab: str = "") -> str:
    """租户领域词表：租户自定义优先，空则平台默认。"""
    v = (vocab or "").strip()
    return v or _DEFAULT_INITIAL_PROMPT


@lru_cache(maxsize=1)
def _get_model():
    from faster_whisper import WhisperModel

    return WhisperModel(_resolve_model_path(settings.asr_model), device="cpu", compute_type="int8")


@lru_cache(maxsize=1)
def _get_small_model():
    from faster_whisper import WhisperModel

    return WhisperModel(_resolve_model_path("small"), device="cpu", compute_type="int8")


def _resolve_model_path(name: str) -> str:
    """优先 data/asr-models/faster-whisper-<name>/ 本地目录（curl 镜像直下），
    回退到 HF Hub 自动下载。"""
    local = settings.data_dir / "asr-models" / f"faster-whisper-{name}"
    if (local / "model.bin").exists():
        return str(local)
    return name


def _is_vocab_overflow(exc: BaseException) -> bool:
    # large-v3 偶发生成词表外 token（Invalid token ID 51865，ctranslate2 抛出）
    return "Invalid token ID" in str(exc)


def _segments_with_fallback(path: str, vocab: str = "") -> list:
    """转写并物化 segments，词表越界时逐级降级：
    ① 去领域 prompt + 断上文条件重试 → ② small 模型兜底（保证出结果）。"""
    try:
        segments, _info = _get_model().transcribe(
            path, language="zh", vad_filter=True, initial_prompt=initial_prompt_for(vocab)
        )
        return list(segments)
    except (RuntimeError, ValueError, IndexError) as exc:
        if not _is_vocab_overflow(exc):
            raise
    try:
        segments, _info = _get_model().transcribe(
            path, language="zh", vad_filter=True,
            initial_prompt=None, condition_on_previous_text=False,
        )
        return list(segments)
    except (RuntimeError, ValueError, IndexError) as exc:
        if not _is_vocab_overflow(exc):
            raise
    segments, _info = _get_small_model().transcribe(
        path, language="zh", vad_filter=True, condition_on_previous_text=False
    )
    return list(segments)


def transcribe(path: str, vocab: str = "") -> str:
    """视频/音频文件 → 中文文本。空结果返回空串。vocab=租户热词表。"""
    lines: list[str] = []
    for seg in _segments_with_fallback(path, vocab):
        text = seg.text.strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def model_name() -> str:
    return settings.asr_model


def transcribe_to_srt(path: str, max_chars: int = 18, max_dur: float = 3.5) -> Optional[str]:
    """视频 → 标准 SRT（P3 使用；单条字幕 ≤max_chars 或超时强制断句）。"""
    segments = _segments_with_fallback(path)
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        start, end = seg.start, seg.end
        # 按长度切分，时长按字符比例摊
        if len(text) > max_chars:
            n = (len(text) + max_chars - 1) // max_chars
            step = (end - start) / n
            for i in range(n):
                cues.append((start + i * step, start + (i + 1) * step,
                             text[i * max_chars:(i + 1) * max_chars]))
        else:
            cues.append((start, end, text))
    if not cues:
        return None
    # 合并过短相邻句
    merged: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        if merged and (len(merged[-1][2]) + len(text) <= max_chars
                       and end - merged[-1][0] <= max_dur):
            ms, _, mt = merged[-1]
            merged[-1] = (ms, end, mt + text)
        else:
            merged.append((start, end, text))
    return _format_srt(merged)


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_srt(cues: list[tuple[float, float, str]]) -> str:
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(blocks)
