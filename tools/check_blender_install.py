"""Exercise Blender's real addon installer in an isolated, disposable profile."""

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile

parser = argparse.ArgumentParser()
for name in ("blender", "addon-zip", "output-dir", "script", "fixture-blend"):
    parser.add_argument("--" + name, required=True, type=Path)
options = parser.parse_args()
output = options.output_dir.resolve()
output.mkdir(parents=True, exist_ok=True)
(output / "result.json").unlink(missing_ok=True)
assert options.fixture_blend.is_file(), "Run the blender_bracket fixture check before installation acceptance"
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
with tempfile.TemporaryDirectory(prefix="profile-", dir=output) as temporary:
    profile = Path(temporary)
    environment = {key: value for key, value in os.environ.items()
        if key != "CASROOT" and not key.startswith("CSF_")
        and key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "PYTHONPATH"}}
    environment.update(ALSOFT_DRIVERS="null", PYTHONNOUSERSITE="1")
    for key, relative in (("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_CONFIG", "config"),
                          ("BLENDER_USER_DATAFILES", "datafiles"), ("BLENDER_USER_EXTENSIONS", "extensions"),
                          ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache"),
                          ("XDG_CONFIG_HOME", "xdg-config"), ("XDG_DATA_HOME", "xdg-data")):
        directory = profile / relative
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    reports = {}
    for phase in ("install", "restart"):
        result = output / f"{phase}.json"
        result.unlink(missing_ok=True)
        fixture = options.fixture_blend.resolve() if phase == "install" else output / "installed-bracket.blend"
        command = [str(options.blender.resolve()), "--background", "--offline-mode"]
        if phase == "install":
            command += ["--factory-startup"]
        command += ["--python-exit-code", "1", "--python", str(options.script.resolve()), "--",
            "--phase", phase, "--profile", str(profile), "--addon-zip", str(options.addon_zip.resolve()),
            "--fixture-blend", str(fixture), "--output-dir", str(output), "--result", str(result)]
        log = output / f"{phase}.log"
        with log.open("w") as stream:
            process = subprocess.run(command, env=environment, cwd=profile,
                stdout=stream, stderr=subprocess.STDOUT, timeout=60, check=False)
        assert process.returncode == 0 and result.is_file(), f"Addon {phase} failed; see {log}"
        reports[phase] = json.loads(result.read_text())
        assert reports[phase]["passed"], reports[phase]
    (output / "result.json").write_text(json.dumps({"passed": True, "offline": True,
        "installer": "preferences.addon_install/addon_enable", "checks": reports}, indent=2))
print("Addon installation: real install/enable, isolated saved preferences, restart auto-enable, bracket edit and STEP passed")
