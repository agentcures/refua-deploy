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
    assert "pip install clawcures-ui" in rendered


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
    assert [command[-1] for command in executed_commands] == list(
        cli_mod._ECOSYSTEM_PACKAGES
    )
    assert all(
        command[0:4] == ["python", "-m", "pip", "install"]
        for command in executed_commands
    )
    assert all("--upgrade" in command for command in executed_commands)


def test_cli_plan_single_machine_includes_single_machine_artifacts(
    tmp_path: Path,
) -> None:
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


def test_cli_apply_dry_run_kubernetes(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    config_path = tmp_path / "apply-k8s.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: apply-k8s",
                "cloud:",
                "  visibility: public",
                "  provider: aws",
                "openclaw:",
                "  base_url: https://openclaw.example.org",
                "runtime:",
                "  orchestrator: kubernetes",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "dist-k8s"

    apply_rc = main(
        [
            "apply",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ]
    )

    assert apply_rc == 0
    rendered = capsys.readouterr().out
    assert "kubectl apply -k" in rendered
    assert (output_dir / "kubernetes" / "kustomization.yaml").exists()


def test_cli_destroy_dry_run_compose(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    config_path = tmp_path / "destroy-compose.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: destroy-compose",
                "cloud:",
                "  visibility: private",
                "  provider: onprem",
                "openclaw:",
                "  base_url: https://openclaw.local",
                "runtime:",
                "  orchestrator: compose",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "dist-compose"

    destroy_rc = main(
        [
            "destroy",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ]
    )

    assert destroy_rc == 0
    rendered = capsys.readouterr().out
    assert "docker compose" in rendered
    assert "down --remove-orphans" in rendered


def test_cli_status_single_machine_outputs_json(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "status-single-machine.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: status-single-machine",
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
    output_dir = tmp_path / "dist-single-machine"

    status_rc = main(
        [
            "status",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert status_rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["orchestrator"] == "single-machine"
    assert payload["scripts"]["run-studio.sh"] is True
    assert payload["env_files"][".env.template"] is True


def test_cli_doctor_single_machine_checks_auth_placeholders(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "doctor-single-machine.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: doctor-single-machine",
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
    output_dir = tmp_path / "dist-doctor-single-machine"

    doctor_rc = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert doctor_rc == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["single_machine_env_has_clawcures_ui_auth_tokens"]["ok"] is True
    assert checks["single_machine_env_has_clawcures_ui_operator_tokens"]["ok"] is True
    assert checks["single_machine_env_has_clawcures_ui_admin_tokens"]["ok"] is True
    assert checks["run_studio_supports_auth_tokens"]["ok"] is True
