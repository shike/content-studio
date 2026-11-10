"""ASR 转写：faster-whisper 封装（懒加载单例，CPU int8）。

首次运行会从 HuggingFace 下载模型（small≈460MB / large-v3≈3GB）；
国内网络慢可在 .env 设置环境后重启，如 export HF_ENDPOINT=https://hf-mirror.com。
"""
from __future__ import annotations

from functools import lru_cache

from .settings import settings

# 口播领域词表默认值（平台创始租户配置）；租户可在品牌配置 asr_vocab 覆盖
_DEFAULT_INITIAL_PROMPT = "以下是中文口播内容，请准确转写为简体中文。"


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


def _beam() -> int:
    """转写搜索宽度：2（默认）比 faster-whisper 默认 5 快约 2 倍，拆解转写给 LLM 读
    质量损失可忽略（2026-10-28 排干加速）；要质量可设 CS_ASR_BEAM=5。"""
    import os

    return max(1, int(os.environ.get("CS_ASR_BEAM", "2")))


def _segments_with_fallback(path: str, vocab: str = "") -> list:
    """转写并物化 segments，词表越界时逐级降级：
    ① 去领域 prompt + 断上文条件重试 → ② small 模型兜底（保证出结果）。"""
    try:
        segments, _info = _get_model().transcribe(
            path, language="zh", vad_filter=True, initial_prompt=initial_prompt_for(vocab),
            beam_size=_beam(),
        )
        return list(segments)
    except (RuntimeError, ValueError, IndexError) as exc:
        if not _is_vocab_overflow(exc):
            raise
    try:
        segments, _info = _get_model().transcribe(
            path, language="zh", vad_filter=True,
            initial_prompt=None, condition_on_previous_text=False, beam_size=_beam(),
        )
        return list(segments)
    except (RuntimeError, ValueError, IndexError) as exc:
        if not _is_vocab_overflow(exc):
            raise
    segments, _info = _get_small_model().transcribe(
        path, language="zh", vad_filter=True, condition_on_previous_text=False, beam_size=_beam()
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
