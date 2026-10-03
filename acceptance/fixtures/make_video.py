#!/usr/bin/env python3
"""生成验收用测试视频（口播形态：画面 + 中文语音）。

依赖：ffmpeg（必需）；macOS `say`（生成中文语音，缺失时降级为无声正弦音，
此时 ASR 相关验收无法产生字幕）。中文 TTS 声音可在
系统设置 → 辅助功能 → 朗读内容 → 系统声音 中确认/安装。

用法：
  python3 make_video.py --check                  # 只检查依赖
  python3 make_video.py --out sample.mp4         # 无声视频
  python3 make_video.py --out sample.mp4 --speech "要说的话"
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--out", default=os.path.join(here, "sample.mp4"))
    ap.add_argument("--speech", default=None, help="要转成语音的文本")
    ap.add_argument("--check", action="store_true", help="只检查依赖后退出")
    args = ap.parse_args()

    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_say = shutil.which("say") is not None

    if args.check:
        print(f"ffmpeg: {'OK' if has_ffmpeg else '缺失（brew install ffmpeg）'}")
        print(f"say(中文语音): {'OK' if has_say else '缺失（P2/P3 语音 fixture 不可用）'}")
        return 0 if has_ffmpeg else 1

    if not has_ffmpeg:
        # 兜底：faster-whisper 用内置 PyAV 解码，不依赖系统 ffmpeg；
        # 无 ffmpeg 但有 say 时直接输出语音文件（内容为音频，扩展名不影响识别）
        if args.speech and has_say:
            ret = subprocess.run(["say", "-o", args.out, args.speech],
                                 capture_output=True, text=True)
            if ret.returncode == 0 and os.path.exists(args.out) and os.path.getsize(args.out) > 0:
                print(args.out)
                return 0
            print(f"say 生成语音失败: {ret.stderr.strip()[:200]}", file=sys.stderr)
            return 1
        print("缺少 ffmpeg，无法生成测试视频", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as td:
        audio_args: list[str]
        if args.speech and has_say:
            aiff = os.path.join(td, "say.aiff")
            ret = subprocess.run(["say", "-o", aiff, args.speech],
                                 capture_output=True, text=True)
            if ret.returncode != 0 or not os.path.exists(aiff):
                print(f"say 生成语音失败: {ret.stderr.strip()[:200]}", file=sys.stderr)
                return 1
            audio_args = ["-i", aiff]
        else:
            audio_args = ["-f", "lavfi", "-i", "sine=frequency=440:duration=6"]

        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30",
               *audio_args, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", args.out]
        ret = subprocess.run(cmd, capture_output=True, text=True)
        if ret.returncode != 0:
            print(f"ffmpeg 失败: {ret.stderr.strip()[-300:]}", file=sys.stderr)
            return 1

    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
