from __future__ import annotations

from pathlib import Path

import yaml

from refua_deploy.config import spec_from_mapping
from refua_deploy.integration import WorkspaceIntegration
from refua_deploy.renderers import render_bundle


def test_render_public_bundle(tmp_path: Path) -> None:
    spec = spec_from_mapping(
        {
            "name": "public-campaign",
            "cloud": {
                "visibility": "public",
                "provider": "aws",
                "region": "us-east-1",
            },
            "openclaw": {
                "base_url": "https://openclaw.example.org",
                "model": "openclaw:main",
            },
            "runtime": {
                "orchestrator": "kubernetes",
                "campaign": {
                    "objective": "Run oncology campaign",
                    "schedule": "0 */4 * * *",
                }
            },
            "kubernetes": {
                "distribution": "eks",
                "ingress_class": "nginx",
                "service_type": "LoadBalancer",
                "storage_class": "gp3",
                "create_network_policy": True,
            },
            "network": {
                "expose_mcp": True,
                "ingress_host": "campaigns.example.org",
            },
        }
    )
    workspace = WorkspaceIntegration(root=tmp_path)

    output_dir = tmp_path / "build-public"
    paths = render_bundle(spec, workspace, output_dir)

    assert (output_dir / "plan.json") in paths
    assert (output_dir / "kubernetes" / "namespace.yaml") in paths
    assert (output_dir / "kubernetes" / "campaign-output-pvc.yaml") in paths
    assert (output_dir / "kubernetes" / "campaign-cronjob.yaml") in paths
    assert (output_dir / "kubernetes" / "network-policy.yaml") in paths
    assert (output_dir / "kubernetes" / "mcp-ingress.yaml") in paths
    assert (output_dir / "kubernetes" / "kustomization.yaml") in paths
    assert (output_dir / "bootstrap" / "cluster-bootstrap.sh") in paths
    assert (output_dir / "bootstrap" / "metadata.auto.json") in paths
    assert (output_dir / "bootstrap" / "network.auto.env") in paths

    configmap_payload = yaml.safe_load((output_dir / "kubernetes" / "configmap.yaml").read_text())
    assert configmap_payload["kind"] == "ConfigMap"
    assert configmap_payload["data"]["REFUA_CAMPAIGN_OPENCLAW_BASE_URL"].startswith("https://")

    cronjob_payload = yaml.safe_load(
        (output_dir / "kubernetes" / "campaign-cronjob.yaml").read_text()
    )
    command = (
        cronjob_payload["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
            "command"
        ]
    )
    assert command[0:2] == ["ClawCures", "run-autonomous"]
    claim_name = (
        cronjob_payload["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"][0][
            "persistentVolumeClaim"
        ]["claimName"]
    )
    assert claim_name == "public-campaign-campaign-output"

    pvc_payload = yaml.safe_load(
        (output_dir / "kubernetes" / "campaign-output-pvc.yaml").read_text()
    )
    assert pvc_payload["spec"]["storageClassName"] == "gp3"

    secret_docs = list(
        yaml.safe_load_all((output_dir / "kubernetes" / "secrets.template.yaml").read_text())
    )
    assert len(secret_docs) == 2
    assert {doc["kind"] for doc in secret_docs} == {"Secret"}

    service_payload = yaml.safe_load((output_dir / "kubernetes" / "mcp-service.yaml").read_text())
    assert service_payload["spec"]["type"] == "LoadBalancer"

    ingress_payload = yaml.safe_load((output_dir / "kubernetes" / "mcp-ingress.yaml").read_text())
    assert ingress_payload["spec"]["ingressClassName"] == "nginx"

    mcp_deployment = yaml.safe_load((output_dir / "kubernetes" / "mcp-deployment.yaml").read_text())
    mcp_env = mcp_deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    env_names = {item["name"] for item in mcp_env}
    assert "REFUA_GPU_MODE" in env_names
    assert "CUDA_VISIBLE_DEVICES" in env_names
    assert "affinity" in mcp_deployment["spec"]["template"]["spec"]

    bootstrap_script = (output_dir / "bootstrap" / "cluster-bootstrap.sh").read_text(
        encoding="utf-8"
    )
    assert "eksctl create cluster" in bootstrap_script


def test_render_private_bundle(tmp_path: Path) -> None:
    spec = spec_from_mapping(
        {
            "name": "private-campaign",
            "cloud": {
                "visibility": "private",
                "provider": "onprem",
            },
            "openclaw": {
                "base_url": "https://openclaw.local",
            },
        }
    )
    workspace = WorkspaceIntegration(root=tmp_path)

    output_dir = tmp_path / "build-private"
    paths = render_bundle(spec, workspace, output_dir)

    assert (output_dir / "private" / "docker-compose.yaml") in paths
    assert (output_dir / "private" / ".env.template") in paths

    compose_payload = yaml.safe_load((output_dir / "private" / "docker-compose.yaml").read_text())
    assert set(compose_payload["services"]) == {"refua_mcp", "campaign_runner"}
    runner_command = compose_payload["services"]["campaign_runner"]["command"]
    assert runner_command[0] == "ClawCures"
    assert "--objective" in runner_command
    assert compose_payload["services"]["refua_mcp"]["environment"]["REFUA_GPU_MODE"] == "auto"
    assert "gpus" not in compose_payload["services"]["refua_mcp"]

    env_text = (output_dir / "private" / ".env.template").read_text(encoding="utf-8")
    assert "OPENCLAW_GATEWAY_TOKEN=replace-me" in env_text
    assert "REFUA_MCP_AUTH_TOKENS=replace-me" in env_text


def test_render_private_kubernetes_bundle(tmp_path: Path) -> None:
    spec = spec_from_mapping(
        {
            "name": "private-k8s",
            "cloud": {
                "visibility": "private",
                "provider": "onprem",
            },
            "openclaw": {
                "base_url": "https://openclaw.internal",
            },
            "runtime": {
                "orchestrator": "kubernetes",
            },
            "kubernetes": {
                "distribution": "k3s",
            },
        }
    )
    workspace = WorkspaceIntegration(root=tmp_path)

    output_dir = tmp_path / "build-private-k8s"
    paths = render_bundle(spec, workspace, output_dir)

    assert (output_dir / "kubernetes" / "namespace.yaml") in paths
    assert (output_dir / "kubernetes" / "campaign-output-pvc.yaml") in paths
    assert (output_dir / "kubernetes" / "kustomization.yaml") in paths
    assert (output_dir / "private" / "docker-compose.yaml") not in paths


def test_render_gpu_required_for_kubernetes_and_compose(tmp_path: Path) -> None:
    k8s_spec = spec_from_mapping(
        {
            "name": "gpu-required",
            "cloud": {"visibility": "public", "provider": "aws"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
            "runtime": {"orchestrator": "kubernetes"},
            "gpu": {
                "mode": "required",
                "count": 2,
                "mcp_enabled": True,
                "campaign_enabled": True,
            },
        }
    )
    workspace = WorkspaceIntegration(root=tmp_path)

    k8s_output = tmp_path / "gpu-required-k8s"
    render_bundle(k8s_spec, workspace, k8s_output)

    mcp_deployment = yaml.safe_load((k8s_output / "kubernetes" / "mcp-deployment.yaml").read_text())
    mcp_resources = mcp_deployment["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert mcp_resources["limits"]["nvidia.com/gpu"] == 2
    assert mcp_resources["requests"]["nvidia.com/gpu"] == 2

    cronjob = yaml.safe_load((k8s_output / "kubernetes" / "campaign-cronjob.yaml").read_text())
    campaign_resources = (
        cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["resources"]
    )
    assert campaign_resources["limits"]["nvidia.com/gpu"] == 2

    compose_spec = spec_from_mapping(
        {
            "name": "gpu-required-compose",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {"orchestrator": "compose"},
            "gpu": {
                "mode": "required",
                "mcp_enabled": True,
                "campaign_enabled": True,
            },
        }
    )
    compose_output = tmp_path / "gpu-required-compose"
    render_bundle(compose_spec, workspace, compose_output)

    compose_payload = yaml.safe_load(
        (compose_output / "private" / "docker-compose.yaml").read_text()
    )
    assert compose_payload["services"]["refua_mcp"]["gpus"] == "all"
    assert compose_payload["services"]["campaign_runner"]["gpus"] == "all"
