from __future__ import annotations

import json
from pathlib import Path

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
