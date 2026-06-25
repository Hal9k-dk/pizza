from pathlib import Path
import subprocess
import shlex

slides = [f"{i:02d}" for i in range(1, 15)]
work = Path("video_build")
slides_dir = work / "slides"
segs = work / "segments"
segs.mkdir(parents=True, exist_ok=True)


def duration(mp3: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(mp3),
        ],
        text=True,
    ).strip()
    return float(out)


segment_paths = []
for num in slides:
    img = slides_dir / f"slide_{num}.png"
    aud = Path("narration_da") / f"slide_{num}.mp3"
    seg = segs / f"slide_{num}.mp4"
    d = duration(aud) + 0.45
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(img),
        "-i",
        str(aud),
        "-filter_complex",
        (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,format=yuv420p,"
            "zoompan=z='min(zoom+0.00018,1.08)':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "d=1:s=1920x1080:fps=30,"
            "fade=t=in:st=0:d=0.5,"
            f"fade=t=out:st={max(d-0.55, 0.1):.3f}:d=0.55[v]"
        ),
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-pix_fmt",
        "yuv420p",
        "-t",
        f"{d:.3f}",
        "-movflags",
        "+faststart",
        str(seg),
    ]
    print(" ".join(shlex.quote(x) for x in cmd))
    subprocess.run(cmd, check=True)
    segment_paths.append(seg)

concat = work / "concat.txt"
concat.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths), encoding="utf-8")

final = Path("8d_report_da.mp4")
cmd = [
    "ffmpeg",
    "-y",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    str(concat),
    "-c",
    "copy",
    str(final),
]
print(" ".join(shlex.quote(x) for x in cmd))
subprocess.run(cmd, check=True)
print(f"Wrote {final}")
