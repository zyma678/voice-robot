import importlib.util
import subprocess
import sys
import tarfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "cloud_runtime_backup.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("cloud_runtime_backup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_runtime_backup(backup_dir: Path) -> None:
    payload = backup_dir / "payload"
    (payload / "config").mkdir(parents=True)
    (payload / "memory" / "vocab_explanations" / "console-vocab" / "tts").mkdir(parents=True)
    (payload / "memory" / "idiom_explanations" / "console-idiom" / "tts").mkdir(parents=True)
    (payload / "config" / "console_settings.json").write_text(
        (
            '{"llm":{"model":"cloud-model"},'
            '"speech":{"tts_voice_type":"cloud-voice"},'
            '"vocab_groups":{"active_group":"一","groups":{"一":["木"]}},'
            '"idiom_groups":{"active_group":"成语","groups":{"成语":["守株待兔"]}}}\n'
        ),
        encoding="utf-8",
    )
    (payload / "memory" / "vocab_explanations" / "console-vocab" / "index.json").write_text(
        (
            '[{"character":"木","word":"木头","explanation":"木字说明",'
            '"source":"cloud","created_at":"a","updated_at":"b",'
            '"tts_path":"vocab_explanations/console-vocab/tts/木.pcm"}]\n'
        ),
        encoding="utf-8",
    )
    (payload / "memory" / "vocab_explanations" / "console-vocab" / "tts" / "木.pcm").write_bytes(
        b"vocab-audio"
    )
    (payload / "memory" / "idiom_explanations" / "console-idiom" / "index.json").write_text(
        (
            '[{"idiom":"守株待兔","pinyin":"shou zhu dai tu",'
            '"explanation":"解释","story":"故事","source":"cloud",'
            '"created_at":"a","updated_at":"b",'
            '"explanation_tts_path":"idiom_explanations/console-idiom/tts/守株待兔-explanation.pcm"}]\n'
        ),
        encoding="utf-8",
    )
    (
        payload
        / "memory"
        / "idiom_explanations"
        / "console-idiom"
        / "tts"
        / "守株待兔-explanation.pcm"
    ).write_bytes(b"idiom-audio")
    with tarfile.open(backup_dir / "runtime.tar.gz", "w:gz") as tar:
        tar.add(payload / "config", arcname="config")
        tar.add(payload / "memory", arcname="memory")


def test_backup_script_exports_learning_data_only_and_no_deploy_env_by_default() -> None:
    module = _load_script_module()

    script = module.build_remote_backup_script(
        backup_id="20260629-120000",
        remote_root="/opt/voice-robot",
        volume_name="deploy_vr-runtime",
        include_env=False,
    )

    assert "docker volume inspect deploy_vr-runtime" in script
    assert "vocab_explanations" in script
    assert "idiom_explanations" in script
    assert "console_settings.json" in script
    assert 'tar czf "$BACKUP_DIR/runtime.tar.gz" -C "$STAGING_DIR" .' in script
    assert 'tar czf "$BACKUP_DIR/runtime.tar.gz" -C "$VOLUME_MOUNT" .' not in script
    assert "/opt/voice-robot/deploy/.env" not in script
    assert "cat /opt/voice-robot/deploy/.env" not in script


def test_import_merges_learning_data_and_keeps_unrelated_runtime(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    project = tmp_path / "project"
    backup_dir.mkdir()
    (project / "runtime" / "config").mkdir(parents=True)
    (project / "runtime" / "memory" / "profiles").mkdir(parents=True)
    (project / "runtime" / "memory" / "vocab_explanations" / "console-vocab").mkdir(parents=True)
    (project / "runtime" / "memory" / "old-only.txt").write_text(
        "local-only\n",
        encoding="utf-8",
    )
    (project / "runtime" / "memory" / "profiles" / "xiaoming.json").write_text(
        '{"user_id":"xiaoming"}\n',
        encoding="utf-8",
    )
    (
        project / "runtime" / "memory" / "vocab_explanations" / "console-vocab" / "local.txt"
    ).write_text("replace me\n", encoding="utf-8")
    (project / "runtime" / "config" / "console_settings.json").write_text(
        '{"bot_profile":{"name":"local"},"llm":{"model":"local-model"},"ui":{"character":"cat"}}\n',
        encoding="utf-8",
    )
    (project / "deploy").mkdir()
    _write_runtime_backup(backup_dir)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "import",
            str(backup_dir),
            "--project-root",
            str(project),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "imported runtime" in completed.stdout
    config = __import__("json").loads(
        (project / "runtime" / "config" / "console_settings.json").read_text(encoding="utf-8")
    )
    assert config["llm"] == {"model": "cloud-model"}
    assert config["speech"] == {"tts_voice_type": "cloud-voice"}
    assert config["vocab_groups"]["active_group"] == "一"
    assert config["idiom_groups"]["active_group"] == "成语"
    assert config["bot_profile"] == {"name": "local"}
    assert config["ui"] == {"character": "cat"}
    assert (project / "runtime" / "memory" / "old-only.txt").exists()
    assert (project / "runtime" / "memory" / "profiles" / "xiaoming.json").exists()
    assert not (
        project / "runtime" / "memory" / "vocab_explanations" / "console-vocab" / "local.txt"
    ).exists()
    assert (
        project / "runtime" / "memory" / "vocab_explanations" / "console-vocab" / "index.json"
    ).exists()
    assert (
        project
        / "runtime"
        / "memory"
        / "idiom_explanations"
        / "console-idiom"
        / "tts"
        / "守株待兔-explanation.pcm"
    ).read_bytes() == b"idiom-audio"
    assert not (project / "deploy" / ".env").exists()

    preimport_archives = list(
        (project / "runtime" / "backups" / "preimport").glob(
            "*/runtime-before-import.tar.gz"
        )
    )
    assert len(preimport_archives) == 1
    with tarfile.open(preimport_archives[0], "r:gz") as tar:
        names = tar.getnames()
    assert "config/console_settings.json" in names
    assert "memory/vocab_explanations/console-vocab/local.txt" in names
    assert "memory/old-only.txt" not in names


def test_import_rejects_unsafe_tar_paths(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    project = tmp_path / "project"
    backup_dir.mkdir()
    project.mkdir()
    with tarfile.open(backup_dir / "runtime.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        payload = b"bad\n"
        info.size = len(payload)
        tar.addfile(info, fileobj=__import__("io").BytesIO(payload))

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "import",
            str(backup_dir),
            "--project-root",
            str(project),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "unsafe tar member" in completed.stderr
    assert not (tmp_path / "escape.txt").exists()
