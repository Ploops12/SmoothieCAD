"""Run the optional real-window acceptance check in an isolated X server."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import signal

parser = argparse.ArgumentParser()
parser.add_argument("--xvfb", required=True)
parser.add_argument("--blender", required=True)
parser.add_argument("--script", required=True)
parser.add_argument("--addon-zip", required=True)
parser.add_argument("--output-dir", required=True, type=Path)
options = parser.parse_args()
options.output_dir.mkdir(parents=True, exist_ok=True)
result_file = options.output_dir / "result.json"
result_file.unlink(missing_ok=True)
command = [options.xvfb, "-a", "-s", "-screen 0 1280x900x24", options.blender,
           "--factory-startup", "--gpu-backend", "opengl", "--enable-event-simulate", "--python", options.script,
           "--", "--addon-zip", options.addon_zip, "--output-dir", str(options.output_dir)]
with (options.output_dir / "blender.log").open("w") as log:
    environment = dict(os.environ)
    environment.pop("WAYLAND_DISPLAY", None)
    environment["XDG_SESSION_TYPE"] = "x11"
    for variable, subdirectory in (("BLENDER_USER_CONFIG", "config"), ("TMPDIR", "runtime")):
        directory = (options.output_dir / subdirectory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(directory)
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
    try:
        returncode = process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise SystemExit(f"Blender UI check timed out; see {options.output_dir / 'blender.log'}")
if returncode or not result_file.exists():
    raise SystemExit(f"Blender UI check failed ({returncode}); see {options.output_dir / 'blender.log'}")
report = json.loads(result_file.read_text())
if not report["passed"]:
    raise SystemExit(report["error"])
print(report.get("summary", "Blender UI check passed: CAD face/edge/vertex selection, perspective snapping, GPU overlays, references and command undo"))
