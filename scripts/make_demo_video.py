"""시연 동영상 후처리: 장면 webm(v1~v6)을 한 편의 mp4로 (시연 동영상 지시서 C).

- 장면마다 scene-<id>.marks.json의 AI 대기 구간만 배속하고(기본 8배속), 배속하는 동안 화면 오른쪽 위에
  "실제 호출 · 8배속"을 띄운다(가짜 AI 테이크면 "가짜 AI · 8배속"). 대기 구간은 잘라 내지 않는다(정직한 편집).
- 맨 앞에 제목 카드(과제명, 실제 호출 여부, 녹화 날짜)를 붙이고 장면을 v1 → v6 순서로 잇는다.
- 출력: 1080p H.264 mp4 (`<out>/demo_final.mp4`). 소리는 넣지 않는다(내레이션은 사람이 따로).

    python -m scripts.make_demo_video [--dir runs/recordings] [--take <이름>] [--speed 8] [--out demo_final.mp4]

녹화(`node scripts/record_demo.mjs --allow-live --scenes v1,v2,v3,v4,v5,v6`) 뒤에 한 번 돌린다. ffmpeg가 없으면 설치 방법을 안내하고 멈춘다.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENES = ["v1", "v2", "v3", "v4", "v5", "v6"]
FONTS = ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "C:/Windows/Fonts/malgun.ttf"]
TITLE = "출동 배정 최적화 · 하네스 기반 AI 에이전트 시연"
MIN_FAST = 3.0      # 이보다 짧은 대기는 배속하지 않는다 (배속 표시가 깜빡이기만 한다)
MERGE_GAP = 1.5     # 대기 구간 사이가 이보다 가까우면 하나로 묶는다 (단계 사이 클릭)
W, H, FPS = 1920, 1080, 30


def need_ffmpeg() -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    sys.exit("ffmpeg가 없습니다. 설치 후 다시 실행하세요: macOS `brew install ffmpeg`, Ubuntu `sudo apt install ffmpeg`, "
             "Windows `winget install Gyan.FFmpeg`")


def font_file(given: str | None) -> str:
    for f in ([given] if given else []) + FONTS:
        if f and Path(f).is_file():
            return f
    sys.exit("한글 글꼴을 찾지 못했습니다. --font로 .ttf/.ttc 경로를 주세요")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def segments(marks: list[dict], total: float) -> list[tuple[float, float, bool]]:
    """[(시작, 끝, 배속 여부)]. 가까운 대기 구간은 묶고, 짧은 대기는 그대로 둔다."""
    fast: list[list[float]] = []
    for m in sorted(marks, key=lambda m: m["start"]):
        s, e = max(0.0, m["start"]), min(total, m["end"])
        if e <= s:
            continue
        if fast and s - fast[-1][1] <= MERGE_GAP:
            fast[-1][1] = max(fast[-1][1], e)
        else:
            fast.append([s, e])
    fast = [f for f in fast if f[1] - f[0] >= MIN_FAST]
    out, t = [], 0.0
    for s, e in fast:
        if s > t:
            out.append((t, s, False))
        out.append((s, e, True))
        t = e
    if t < total:
        out.append((t, total, False))
    return out


def esc(text: str) -> str:
    """drawtext 문자열 이스케이프 (작은따옴표 안)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def encode_scene(src: Path, marks: list[dict], dst: Path, speed: float, font: str, live: bool) -> dict:
    total = duration(src)
    segs = segments(marks, total)
    label = esc(f"{'실제 호출' if live else '가짜 AI'} · {speed:g}배속")
    parts, chains = [], []
    for i, (s, e, fast) in enumerate(segs):
        chain = f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS"
        if fast:
            chain += (f",setpts=PTS/{speed},drawtext=fontfile='{font}':text='{label}':fontsize=34:fontcolor=white"
                      f":box=1:boxcolor=0x141413@0.85:boxborderw=14:x=w-tw-40:y=36")
        chains.append(chain + f",fps={FPS},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                      f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0xfafafa,setsar=1[p{i}]")
        parts.append(f"[p{i}]")
    graph = ";".join(chains) + f";{''.join(parts)}concat=n={len(parts)}:v=1:a=0,format=yuv420p[out]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[out]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), "-an", str(dst)], check=True)
    sped = sum(e - s for s, e, f in segs if f)
    return {"source_seconds": round(total, 1), "sped_seconds": round(sped, 1),
            "output_seconds": round(total - sped + sped / speed, 1), "fast_spans": sum(1 for *_, f in segs if f)}


def title_card(dst: Path, font: str, live: bool, when: str, seconds: float = 4.0) -> None:
    lines = [(TITLE, 64, -90), ("회사 Azure OpenAI 실제 호출" if live else "가짜 AI(리허설) 녹화 — 실제 호출 아님", 40, 10),
             (f"녹화 {when} · AI 대기 구간은 배속 표시와 함께 빨리 감기", 30, 80)]
    draw = ",".join(f"drawtext=fontfile='{font}':text='{esc(t)}':fontsize={size}:fontcolor=white:x=(w-tw)/2:y=(h-th)/2+{dy}"
                    for t, size, dy in lines)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x141413:s={W}x{H}:d={seconds}:r={FPS}",
                    "-vf", f"{draw},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
                    "-an", str(dst)], check=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="시연 장면 → 한 편의 mp4 (대기 구간 배속)")
    ap.add_argument("--dir", default=str(ROOT / "runs" / "recordings"), help="장면 webm·marks·takes.json이 있는 폴더")
    ap.add_argument("--take", default=None, help="takes.json의 테이크 이름 (없으면 마지막 테이크)")
    ap.add_argument("--speed", type=float, default=8.0)
    ap.add_argument("--out", default="demo_final.mp4")
    ap.add_argument("--font", default=None)
    args = ap.parse_args(argv)
    need_ffmpeg()
    font = font_file(args.font)
    folder = Path(args.dir)
    takes = json.loads((folder / "takes.json").read_text(encoding="utf-8")) if (folder / "takes.json").is_file() else []
    take = next((t for t in takes if t["take"] == args.take), takes[-1] if takes else {})
    live = bool(take.get("live"))
    when = (take.get("started") or time.strftime("%Y-%m-%d"))[:10]

    parts = folder / ".parts"
    parts.mkdir(exist_ok=True)
    clips = [parts / "00-title.mp4"]
    title_card(clips[0], font, live, when)
    report = {}
    for sid in SCENES:
        src = folder / f"scene-{sid}.webm"
        if not src.is_file():
            print(f"장면 {sid}: 없음 (건너뜀)")
            continue
        mpath = folder / f"scene-{sid}.marks.json"
        marks = json.loads(mpath.read_text(encoding="utf-8"))["marks"] if mpath.is_file() else []
        dst = parts / f"{sid}.mp4"
        report[sid] = encode_scene(src, marks, dst, args.speed, font, live)
        clips.append(dst)
        r = report[sid]
        print(f"장면 {sid}: {r['source_seconds']}초 → {r['output_seconds']}초 (배속 구간 {r['fast_spans']}개, {r['sped_seconds']}초)")
    listing = parts / "list.txt"
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    out = folder / args.out
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)
    total = duration(out)
    (folder / "demo_final.json").write_text(json.dumps({"take": take.get("take"), "live": live, "speed": args.speed,
                                                        "seconds": round(total, 1), "scenes": report},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완성: {out} ({total / 60:.1f}분, {'실제 호출' if live else '가짜 AI 리허설'})")


if __name__ == "__main__":
    main()
