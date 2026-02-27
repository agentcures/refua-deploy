from __future__ import annotations

import json
from pathlib import Path

from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

import refua_deploy.cli as cli_mod
from refua_deploy.cli import main


def test_cli_init_plan_and_render(tmp_path: Path) -> None:
    config_path = tmp_path / "deploy.yaml"
    plan_path = tmp_path / "plan.json"
    output_dir = tmp_path / "artifacts"

    init_rc = main(
        [
            "init",
            "--output",
            str(config_path),
            "--name",
            "campaign-prod",
            "--visibility",
            "public",
            "--provider",
            "aws",
            "--gpu-mode",
            "required",
            "--gpu-vendor",
            "nvidia",
            "--workspace-root",
            str(tmp_path),
        ]
    )
    assert init_rc == 0
    assert config_path.exists()

    plan_rc = main(
        [
            "plan",
            "--config",
            str(config_path),
            "--workspace-root",
            str(tmp_path),
            "--output",
            str(plan_path),
        ]
    )
    assert plan_rc == 0

    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_payload["name"] == "campaign-prod"
    assert plan_payload["cloud"]["visibility"] == "public"
    assert plan_payload["runtime"]["orchestrator"] == "kubernetes"
    assert plan_payload["kubernetes"]["distribution"] == "eks"
    assert plan_payload["gpu"]["mode"] == "required"
    assert plan_payload["gpu"]["vendor"] == "nvidia"
    assert plan_payload["automation"]["provisioning_level"] == "auto"
    assert plan_payload["automation"]["cluster_name"] == "campaign-prod-aws"
    assert "metadata.auto.json" in "\n".join(plan_payload["artifacts"])

    render_rc = main(
        [
            "render",
            "--config",
            str(config_path),
            "--workspace-root",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert render_rc == 0
    assert (output_dir / "plan.json").exists()
    assert (output_dir / "kubernetes" / "namespace.yaml").exists()


def test_cli_install_ecosystem_dry_run(capsys: CaptureFixture[str]) -> None:
    install_rc = main(
        [
            "install-ecosystem",
            "--dry-run",
        ]
    )

    assert install_rc == 0
    rendered = capsys.readouterr().out
    assert "pip install refua-studio" in rendered


def test_cli_install_ecosystem_executes_expected_package_order(
    monkeypatch: MonkeyPatch,
) -> None:
    executed_commands: list[list[str]] = []

    def _fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        executed_commands.append(command)

    monkeypatch.setattr("refua_deploy.cli.subprocess.run", _fake_run)
    install_rc = main(
        [
            "install-ecosystem",
            "--python",
            "python",
            "--upgrade",
        ]
    )

    assert install_rc == 0
    assert [command[-1] for command in executed_commands] == list(cli_mod._ECOSYSTEM_PACKAGES)
    assert all(command[0:4] == ["python", "-m", "pip", "install"] for command in executed_commands)
    assert all("--upgrade" in command for command in executed_commands)


def test_cli_plan_single_machine_includes_single_machine_artifacts(tmp_path: Path) -> None:
    config_path = tmp_path / "single-machine.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: single-machine",
                "cloud:",
                "  visibility: private",
                "  provider: onprem",
                "openclaw:",
                "  base_url: https://openclaw.local",
                "runtime:",
                "  orchestrator: single-machine",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "single-machine-plan.json"

    plan_rc = main(
        [
            "plan",
            "--config",
            str(config_path),
            "--output",
            str(plan_path),
        ]
    )

    assert plan_rc == 0
    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert "single-machine/install-ecosystem.sh" in plan_payload["artifacts"]
