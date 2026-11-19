"""ClipStudio from the command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, captions, config, models, pipeline, transcribe
from . import project as projects
from .download import DownloadError
from .media import MediaError
from .timestamps import TimestampError, parse_ranges

EXAMPLES = """examples:
  clipstudio make "https://www.youtube.com/watch?v=VIDEO_ID" 1:23-2:05
  clipstudio make "https://youtu.be/VIDEO_ID" 0:45-1:30 12:10-12:55 --layout crop
  clipstudio make "C:\\Videos\\stream.mp4" 1:02:03-1:02:45 --lang hi
  clipstudio render projects\\VIDEO_ID_01m23s-02m05s

Put the link in quotes: PowerShell treats & in a link as a command."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipstudio",
        description="Cut a YouTube video (or a video file) into 9:16 clips with word-by-word captions in Hindi or English.",
        epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"clipstudio {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    make = sub.add_parser("make", help="make clips from a link or file and time ranges",
                          epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    make.add_argument("source", help="a YouTube link, or the path of a video file on this PC")
    make.add_argument("ranges", nargs="+", metavar="START-END",
                      help="parts to cut, like 1:23-2:05 (several allowed, separated by spaces or commas)")
    make.add_argument("--lang", choices=["auto", "hi", "en"], default="auto",
                      help="spoken language; auto picks Hindi or English (default: auto)")
    make.add_argument("--layout", choices=["blur", "crop"], default="blur",
                      help="blur: the whole picture on a blurred background; crop: fill the screen and cut the sides "
                           "(default: blur)")
    make.add_argument("--style", choices=sorted(captions.STYLES), default=captions.DEFAULT_STYLE,
                      help=f"caption style (default: {captions.DEFAULT_STYLE})")
    make.add_argument("--model", default=config.DEFAULT_MODEL,
                      help=f"speech model (default: {config.DEFAULT_MODEL}); small is faster but makes more mistakes")
    make.add_argument("--pad", type=float, default=config.PAD_SECONDS,
                      help=f"extra seconds kept on each side, so the cut can be moved later (default: {config.PAD_SECONDS:g})")
    make.add_argument("--out", default=str(config.PROJECTS_DIR), help="folder for the projects (default: projects)")
    make.add_argument("--cookies-from-browser", metavar="BROWSER",
                      help="use this browser's YouTube sign-in (firefox, chrome or edge) when YouTube asks for one")

    again = sub.add_parser("render", help="render a project again after editing its project.json")
    again.add_argument("project", help="a project folder, or its project.json")

    setup = sub.add_parser("setup", help="download the speech model now instead of on first use")
    setup.add_argument("--model", default=config.DEFAULT_MODEL, help=f"(default: {config.DEFAULT_MODEL})")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")    # Hindi words print in any terminal
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    try:
        if args.command == "make":
            ranges = parse_ranges(args.ranges)
            finished = pipeline.make(
                args.source, ranges, language=None if args.lang == "auto" else args.lang, layout=args.layout,
                style=args.style, model=args.model, pad=max(0.0, args.pad), out_dir=Path(args.out),
                cookies_browser=args.cookies_from_browser)
            print("\nFinished clips:")
            for clip in finished:
                print(f"  {clip}")
            print("To change a clip, edit project.json in its folder, then run: clipstudio render <folder>")
        elif args.command == "render":
            print(f"Rendered: {pipeline.rerender(args.project)}")
        elif args.command == "setup":
            print(f"Getting the speech model {args.model} ready. The first time downloads it "
                  "(about 1.6 GB for large-v3-turbo); if it stops, run this again to continue.")
            folder = models.ensure(args.model, on_progress=models.progress_printer())
            print("Checking it loads...")
            transcribe.load_model(args.model)
            print(f"Ready: {folder}")
        return 0
    except (TimestampError, DownloadError, MediaError, projects.ProjectError, models.ModelError,
            FileNotFoundError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
