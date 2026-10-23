#!/usr/bin/env python3
"""Back up Volcengine runtime data/config and import it into local runtime."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from shlex import quote


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return default
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() == name:
            return candidate.strip().strip("\"'")
    return default


DEFAULT_HOST = env_value("VR_DEPLOY_HOST")
DEFAULT_REMOTE_ROOT = env_value("VR_DEPLOY_REMOTE_ROOT", "/opt/voice-robot")
DEFAULT_VOLUME_NAME = env_value("VR_DEPLOY_VOLUME", "deploy_vr-runtime")
DEFAULT_OUTPUT_ROOT = Path("runtime/backups/cloud")
CONFIG_KEYS = ("llm", "speech", "vocab_groups", "idiom_groups")
LEARNING_MEMORY_DIRS = ("vocab_explanations", "idiom_explanations")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class BackupError(RuntimeError):
    pass


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def require_safe_token(value: str, name: str) -> None:
    if not SAFE_TOKEN_RE.match(value):
        raise BackupError(f"{name} contains unsupported characters: {value!r}")


def build_remote_backup_script(
    *,
    backup_id: str,
    remote_root: str,
    volume_name: str,
    include_env: bool,
) -> str:
    require_safe_token(backup_id, "backup_id")
    require_safe_token(volume_name, "volume_name")
    env_file = f"{remote_root.rstrip('/')}/.env"
    include_env_block = ""
    if include_env:
        include_env_block = f"""
if [ -f {quote(env_file)} ]; then
  cp {quote(env_file)} "$BACKUP_DIR/deploy.env"
  chmod 600 "$BACKUP_DIR/deploy.env"
  ENV_STATUS=present
else
  ENV_STATUS=missing
fi
"""
    else:
        include_env_block = "ENV_STATUS=skipped\n"

    return f"""set -eu
umask 077
BACKUP_ID={quote(backup_id)}
REMOTE_ROOT={quote(remote_root)}
VOLUME_NAME={quote(volume_name)}
BACKUP_DIR="/tmp/voice-robot-backup-${{BACKUP_ID}}"
STAGING_DIR="$BACKUP_DIR/runtime"
rm -rf "$BACKUP_DIR"
mkdir -p "$STAGING_DIR/config" "$STAGING_DIR/memory"
VOLUME_MOUNT=$(docker volume inspect {volume_name} --format '{{{{.Mountpoint}}}}')
test -d "$VOLUME_MOUNT"
if [ -f "$VOLUME_MOUNT/config/console_settings.json" ]; then
  python3 - "$VOLUME_MOUNT/config/console_settings.json" "$STAGING_DIR/config/console_settings.json" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
allowed = {{"llm", "speech", "vocab_groups", "idiom_groups"}}
try:
    data = json.loads(source.read_text(encoding="utf-8"))
except Exception:
    data = {{}}
if not isinstance(data, dict):
    data = {{}}
payload = {{key: data[key] for key in sorted(allowed) if key in data}}
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
else
  printf '{{}}\\n' > "$STAGING_DIR/config/console_settings.json"
fi
for dir in vocab_explanations idiom_explanations; do
  if [ -d "$VOLUME_MOUNT/memory/$dir" ]; then
    cp -a "$VOLUME_MOUNT/memory/$dir" "$STAGING_DIR/memory/"
  fi
done
tar czf "$BACKUP_DIR/runtime.tar.gz" -C "$STAGING_DIR" .
rm -rf "$STAGING_DIR"
{include_env_block.rstrip()}
ARCHIVE_LIST=$(tar tzf "$BACKUP_DIR/runtime.tar.gz")
RUNTIME_SIZE_KB=$(du -sk "$BACKUP_DIR/runtime.tar.gz" | awk '{{print $1}}')
RUNTIME_FILE_COUNT=$(printf '%s\\n' "$ARCHIVE_LIST" | grep -v '/$' | wc -l | tr -d ' ')
RUNTIME_BAK_FILE_COUNT=0
CONFIG_STATUS=missing
CONFIG_STATUS=$(python3 - "$BACKUP_DIR/runtime.tar.gz" <<'PY'
import json
import sys
import tarfile

try:
    with tarfile.open(sys.argv[1], "r:gz") as tar:
        member = tar.extractfile("config/console_settings.json")
        if member is None:
            member = tar.extractfile("./config/console_settings.json")
        if member is None:
            raise FileNotFoundError("config/console_settings.json")
        json.loads(member.read().decode("utf-8"))
except Exception as exc:
    print("invalid:" + type(exc).__name__)
else:
    print("valid")
PY
)
export BACKUP_ID REMOTE_ROOT VOLUME_NAME BACKUP_DIR VOLUME_MOUNT ENV_STATUS
export RUNTIME_SIZE_KB RUNTIME_FILE_COUNT RUNTIME_BAK_FILE_COUNT CONFIG_STATUS
python3 - "$BACKUP_DIR/manifest.json" <<'PY'
import json
import os
import socket
import sys
from datetime import datetime, timezone

manifest = {{
    "backup_id": os.environ["BACKUP_ID"],
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_host": socket.gethostname(),
    "remote_root": os.environ["REMOTE_ROOT"],
    "volume_name": os.environ["VOLUME_NAME"],
    "volume_mount": os.environ["VOLUME_MOUNT"],
    "runtime_archive": "runtime.tar.gz",
    "scope": "learning-config-and-tts-cache",
    "config_keys": ["idiom_groups", "llm", "speech", "vocab_groups"],
    "memory_dirs": ["idiom_explanations", "vocab_explanations"],
    "deploy_env": os.environ["ENV_STATUS"],
    "runtime_size_kb": int(os.environ.get("RUNTIME_SIZE_KB") or 0),
    "runtime_file_count": int(os.environ.get("RUNTIME_FILE_COUNT") or 0),
    "runtime_bak_file_count": int(os.environ.get("RUNTIME_BAK_FILE_COUNT") or 0),
    "console_settings_json": os.environ["CONFIG_STATUS"],
}}
path = sys.argv[1]
with open(path, "w", encoding="utf-8") as file:
    json.dump(manifest, file, ensure_ascii=True, indent=2, sort_keys=True)
    file.write("\\n")
PY
(cd "$BACKUP_DIR" && for f in runtime.tar.gz deploy.env manifest.json; do
  [ -f "$f" ] && sha256sum "$f"
done > SHA256SUMS)
printf 'REMOTE_BACKUP_DIR=%s\\n' "$BACKUP_DIR"
"""


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise BackupError(f"command failed ({completed.returncode}): {' '.join(cmd)}\n{details}")
    return completed


def parse_remote_backup_dir(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("REMOTE_BACKUP_DIR="):
            value = line.split("=", 1)[1].strip()
            if value.startswith("/tmp/voice-robot-backup-"):
                return value
    raise BackupError("remote backup did not report REMOTE_BACKUP_DIR")


def command_backup(args: argparse.Namespace) -> int:
    if not args.host:
        raise BackupError("SSH host is required: set VR_DEPLOY_HOST or pass --host")
    backup_id = args.backup_id or timestamp()
    output_root = Path(args.output_dir)
    backup_dir = output_root / backup_id
    backup_dir.mkdir(parents=True, exist_ok=False)

    remote_script = build_remote_backup_script(
        backup_id=backup_id,
        remote_root=args.remote_root,
        volume_name=args.volume_name,
        include_env=args.include_env,
    )
    print(f"creating remote backup on {args.host} ...")
    completed = run_command(
        ["ssh", args.host, remote_script],
        capture_output=True,
    )
    remote_dir = parse_remote_backup_dir(completed.stdout)
    if args.remote_only:
        print(f"remote backup saved to {remote_dir}")
        return 0

    try:
        source = f"{args.host}:{remote_dir}/"
        # The remote payload already contains gzip archives; rsync compression
        # makes large runtime backups much slower on small ECS instances.
        run_command(["rsync", "-a", source, str(backup_dir) + "/"])
    finally:
        if not args.keep_remote_temp:
            cleanup = f"rm -rf {quote(remote_dir)}"
            run_command(["ssh", args.host, cleanup], capture_output=True)

    print(f"backup saved to {backup_dir}")
    inspect_backup_dir(backup_dir, print_summary=True)
    return 0


def resolve_backup_paths(path: Path) -> tuple[Path, Path | None, Path]:
    if path.is_dir():
        runtime_archive = path / "runtime.tar.gz"
        deploy_env = path / "deploy.env"
        return runtime_archive, deploy_env if deploy_env.exists() else None, path
    return path, None, path.parent


def validate_tar_member(member: tarfile.TarInfo) -> None:
    name = member.name
    posix_path = PurePosixPath(name)
    if not name or posix_path.is_absolute() or ".." in posix_path.parts:
        raise BackupError(f"unsafe tar member: {name}")
    if member.issym() or member.islnk():
        raise BackupError(f"unsafe tar member: {name}")
    if not (member.isfile() or member.isdir()):
        raise BackupError(f"unsafe tar member: {name}")


def extract_runtime_archive(runtime_archive: Path, staging_dir: Path) -> None:
    if not runtime_archive.exists():
        raise BackupError(f"runtime archive not found: {runtime_archive}")
    with tarfile.open(runtime_archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            validate_tar_member(member)
        tar.extractall(staging_dir, members=members)


def import_target_paths(staging_dir: Path) -> list[Path]:
    targets: list[Path] = []
    config_path = staging_dir / "config" / "console_settings.json"
    if config_path.exists():
        targets.append(Path("config/console_settings.json"))
    for dirname in LEARNING_MEMORY_DIRS:
        path = staging_dir / "memory" / dirname
        if path.exists():
            targets.append(Path("memory") / dirname)
    return targets


def add_existing_targets_to_preimport_archive(
    *,
    runtime_dir: Path,
    target_paths: list[Path],
    archive_path: Path,
) -> bool:
    existing = [
        runtime_dir / rel_path
        for rel_path in target_paths
        if (runtime_dir / rel_path).exists()
    ]
    if not existing:
        return False
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in existing:
            tar.add(item, arcname=item.relative_to(runtime_dir).as_posix())
    return True


def load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def merge_console_settings(*, source: Path, target: Path) -> None:
    incoming = load_json_object(source)
    existing = load_json_object(target)
    for key in CONFIG_KEYS:
        if key in incoming:
            existing[key] = incoming[key]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def replace_runtime_path(*, source: Path, target: Path) -> None:
    if target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def import_learning_runtime(*, staging_dir: Path, runtime_dir: Path) -> None:
    config_source = staging_dir / "config" / "console_settings.json"
    if config_source.exists():
        merge_console_settings(
            source=config_source,
            target=runtime_dir / "config" / "console_settings.json",
        )
    for dirname in LEARNING_MEMORY_DIRS:
        source = staging_dir / "memory" / dirname
        if source.exists():
            replace_runtime_path(
                source=source,
                target=runtime_dir / "memory" / dirname,
            )


def apply_deploy_env(
    *,
    deploy_env: Path,
    project_root: Path,
    backup_id: str,
) -> None:
    target = project_root / ".env"
    if target.exists():
        backup_path = project_root / f".env.preimport-{backup_id}.bak"
        shutil.copy2(target, backup_path)
        os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)
    shutil.copy2(deploy_env, target)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def command_import(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    runtime_archive, deploy_env, backup_dir = resolve_backup_paths(Path(args.backup).resolve())
    backup_id = backup_dir.name if backup_dir.name else timestamp()
    runtime_dir = project_root / "runtime"
    with tempfile.TemporaryDirectory(prefix="voice-robot-runtime-import-") as temp:
        staging_dir = Path(temp) / "runtime"
        staging_dir.mkdir()
        extract_runtime_archive(runtime_archive, staging_dir)
        target_paths = import_target_paths(staging_dir)
        if not target_paths:
            raise BackupError("runtime archive is empty")

        preimport_archive = (
            runtime_dir
            / "backups"
            / "preimport"
            / timestamp()
            / "runtime-before-import.tar.gz"
        )
        created_preimport = add_existing_targets_to_preimport_archive(
            runtime_dir=runtime_dir,
            target_paths=target_paths,
            archive_path=preimport_archive,
        )
        import_learning_runtime(
            staging_dir=staging_dir,
            runtime_dir=runtime_dir,
        )

    env_status = "not applied"
    if args.apply_env:
        if deploy_env is None:
            raise BackupError("backup does not contain deploy.env")
        apply_deploy_env(deploy_env=deploy_env, project_root=project_root, backup_id=backup_id)
        env_status = "applied to .env"

    print(f"imported runtime from {runtime_archive} into {runtime_dir}")
    if created_preimport:
        print(f"preimport backup saved to {preimport_archive}")
    else:
        print("preimport backup skipped: no existing matching runtime entries")
    print(f"deploy env: {env_status}")
    return 0


def inspect_backup_dir(backup_dir: Path, *, print_summary: bool) -> dict:
    manifest_path = backup_dir / "manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_archive = backup_dir / "runtime.tar.gz"
    deploy_env = backup_dir / "deploy.env"
    summary = {
        "backup_dir": str(backup_dir),
        "runtime_archive": str(runtime_archive),
        "runtime_archive_bytes": runtime_archive.stat().st_size if runtime_archive.exists() else 0,
        "deploy_env": "present" if deploy_env.exists() else "missing",
        "manifest": manifest,
    }
    if print_summary:
        print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return summary


def command_inspect(args: argparse.Namespace) -> int:
    path = Path(args.backup).resolve()
    backup_dir = path if path.is_dir() else path.parent
    inspect_backup_dir(backup_dir, print_summary=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up Volcengine voice-robot runtime data/config and import it locally.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Pull learning config and generated TTS cache from ECS")
    backup.add_argument("--host", default=DEFAULT_HOST, help=f"SSH host, default: {DEFAULT_HOST}")
    backup.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    backup.add_argument("--volume-name", default=DEFAULT_VOLUME_NAME)
    backup.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    backup.add_argument("--backup-id", default="")
    backup.add_argument(
        "--include-env",
        action="store_true",
        help="Also copy remote root .env; disabled by default because it contains secrets",
    )
    backup.add_argument("--keep-remote-temp", action="store_true")
    backup.add_argument(
        "--remote-only",
        action="store_true",
        help="Create the remote backup package and print its path without downloading it",
    )
    backup.set_defaults(func=command_backup)

    import_cmd = subparsers.add_parser("import", help="Import a backup into local runtime/")
    import_cmd.add_argument("backup", help="Backup directory or runtime.tar.gz path")
    import_cmd.add_argument("--project-root", default=".")
    import_cmd.add_argument(
        "--apply-env",
        action="store_true",
        help="Also copy backup deploy.env to local root .env after backing up any existing file",
    )
    import_cmd.set_defaults(func=command_import)

    inspect = subparsers.add_parser("inspect", help="Print backup manifest without showing secrets")
    inspect.add_argument("backup", help="Backup directory or file path inside it")
    inspect.set_defaults(func=command_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
