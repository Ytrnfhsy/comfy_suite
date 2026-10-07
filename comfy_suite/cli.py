"""Command line: start the panel, launch PhotoSuite wired for it, or generate headlessly."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .settings import Settings


def _bridge(settings: Settings):
    from .photosuite import ControlClient, PhotoSuiteBridge, read_token

    token = read_token(settings.photosuite_token, None if settings.photosuite_token else settings.token_file_path())
    client = ControlClient(settings.photosuite_host, settings.photosuite_port, token)
    client.connect()
    return PhotoSuiteBridge(client, settings.exchange_path())


def _generator(settings: Settings):
    from .comfy import ComfyClient
    from .generator import Generator

    return Generator(_bridge(settings), ComfyClient(settings.comfy_url), settings)


def _progress(value: float, text: str) -> None:
    bar = "#" * int(value * 30)
    print(f"\r[{bar:<30}] {text:<24}", end="", flush=True)


def find_photosuite(settings: Settings) -> str | None:
    if settings.photosuite_executable:
        return settings.photosuite_executable
    for name in ("photosuite", "PhotoSuite"):
        found = shutil.which(name)
        if found:
            return found
    mac = Path("/Applications/PhotoSuite.app/Contents/MacOS/photosuite")
    return str(mac) if mac.exists() else None


def launch_photosuite(settings: Settings, executable: str | None, files: list[str]) -> subprocess.Popen:
    exe = executable or find_photosuite(settings)
    if not exe:
        raise SystemExit("PhotoSuite was not found; pass --photosuite PATH or set it in the settings")
    exchange = settings.exchange_path()
    exchange.mkdir(parents=True, exist_ok=True)
    token_file = settings.token_file_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "--control",
        str(settings.photosuite_port),
        "--control-token-file",
        str(token_file),
        "--automation-read-root",
        str(exchange),
        "--automation-write-root",
        str(exchange),
        *files,
    ]
    print("starting:", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    # PhotoSuite creates the token file on start-up; wait for it.
    for _ in range(100):
        if token_file.exists() and token_file.read_text(encoding="utf-8").strip():
            break
        time.sleep(0.1)
    return proc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="comfy-suite", description="Generative AI (ComfyUI) for PhotoSuite")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--comfy", help="ComfyUI URL (default from settings)")
    p.add_argument("--port", type=int, help="PhotoSuite control port")
    p.add_argument("--token-file", help="PhotoSuite control token file")
    p.add_argument("--exchange", help="exchange folder (PhotoSuite's automation root)")
    sub = p.add_subparsers(dest="cmd")

    ui = sub.add_parser("ui", help="open the panel (default)")
    ui.add_argument("--workflow", help="load a custom workflow into the Custom Graph workspace")

    launch = sub.add_parser("launch", help="start PhotoSuite with the control channel enabled, then the panel")
    launch.add_argument("--photosuite", help="path to the PhotoSuite executable")
    launch.add_argument("--no-ui", action="store_true", help="only start PhotoSuite")
    launch.add_argument("files", nargs="*", help="files to open")

    gen = sub.add_parser("generate", help="generate into the active document without the panel")
    gen.add_argument("prompt", nargs="?", default="")
    gen.add_argument("--negative", default="")
    gen.add_argument("--style", default="")
    gen.add_argument("--strength", type=float, default=1.0, help="0..1; below 1 refines the existing pixels")
    gen.add_argument("--seed", type=int, default=-1)
    gen.add_argument("--batch", type=int, default=1)
    gen.add_argument("--no-selection", action="store_true", help="ignore the selection")
    gen.add_argument("--apply", type=int, default=0, help="which result to add as a layer (-1: none)")

    up = sub.add_parser("upscale", help="upscale the active document into a new one")
    up.add_argument("--factor", type=float, default=2.0)
    up.add_argument("--model", default="", help="upscale model file (default: Lanczos)")
    up.add_argument("--refine", type=float, default=0.0, help="refine strength after upscaling (0 = off)")
    up.add_argument("--style", default="")

    sub.add_parser("info", help="check both connections and list the server's models")

    args = p.parse_args(argv)
    settings = Settings.load()
    if args.comfy:
        settings.comfy_url = args.comfy
    if args.port:
        settings.photosuite_port = args.port
    if args.token_file:
        settings.photosuite_token_file = args.token_file
    if args.exchange:
        settings.exchange_dir = args.exchange

    if args.cmd in (None, "ui"):
        from .ui.app import run

        return run(settings, getattr(args, "workflow", None))

    if args.cmd == "launch":
        proc = launch_photosuite(settings, args.photosuite, args.files)
        if args.no_ui:
            return proc.wait()
        from .ui.app import run

        return run(settings)

    if args.cmd == "info":
        from .comfy import ComfyClient, ComfyError
        from .photosuite import PhotoSuiteError

        ok = True
        try:
            comfy = ComfyClient(settings.comfy_url)
            stats = comfy.system_stats()
            m = comfy.models()
            print(f"ComfyUI {settings.comfy_url}: {stats.get('system', {}).get('comfyui_version', 'ok')}")
            for label, items in (("checkpoints", m.checkpoints), ("loras", m.loras), ("controlnets", m.controlnets), ("upscalers", m.upscalers)):
                print(f"  {label}: {len(items)}" + (f" ({', '.join(items[:5])}{', …' if len(items) > 5 else ''})" if items else ""))
        except ComfyError as e:
            ok = False
            print(f"ComfyUI: {e}")
        try:
            s = _bridge(settings).session()
            print(f"PhotoSuite :{settings.photosuite_port}: {len(s.get('documents', []))} document(s) open")
        except (PhotoSuiteError, OSError) as e:
            ok = False
            print(f"PhotoSuite: {e}")
        return 0 if ok else 1

    from .generator import GenerateParams
    from .styles import StyleLibrary

    styles = StyleLibrary(settings.styles_path())
    style = styles.get(args.style or settings.style)
    gen_ = _generator(settings)
    if args.cmd == "generate":
        params = GenerateParams(args.prompt, args.negative, args.strength, args.seed, args.batch, use_selection=not args.no_selection)
        result = gen_.generate(style, params, _progress)
        print(f"\n{len(result.images)} image(s), seed {result.seed}, region {result.region}")
        if 0 <= args.apply < len(result.images):
            layer = gen_.apply(result, args.apply)
            print(f"added layer {layer}")
        return 0
    if args.cmd == "upscale":
        result = gen_.upscale(style, args.factor, args.model, args.refine > 0, args.refine, "", _progress)
        gen_.apply(result)
        print(f"\nopened {result.region.width}×{result.region.height}")
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
