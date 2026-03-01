from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from refua_deploy.config import load_spec


def _write_yaml(path: Path, payload: dict) -> None:  # type: ignore[type-arg]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_load_spec_public_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "public.yaml"
    _write_yaml(
        config_path,
        {
            "name": "oncology-prod",
            "cloud": {"visibility": "public", "provider": "aws", "region": "us-east-1"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
        },
    )

    spec = load_spec(config_path)

    assert spec.name == "oncology-prod"
    assert spec.cloud.visibility == "public"
    assert spec.cloud.provider == "aws"
    assert spec.runtime.namespace == "oncology-prod"
    assert spec.runtime.orchestrator == "kubernetes"
    assert spec.runtime.campaign.max_rounds == 4
    assert spec.runtime.mcp.mode == "inprocess"
    assert spec.runtime.mcp.port == 8000
    assert spec.kubernetes.distribution == "eks"
    assert spec.kubernetes.service_type == "ClusterIP"
    assert spec.gpu.mode == "auto"
    assert spec.gpu.vendor == "nvidia"
    assert spec.gpu.resource_name == "nvidia.com/gpu"
    assert spec.gpu.mcp_enabled is True
    assert spec.gpu.campaign_enabled is False
    assert spec.automation.auto_discover_network is True
    assert spec.automation.bootstrap_cluster is True
    assert spec.automation.provisioning_level == "auto"
    assert spec.automation.node_count == 3


def test_load_spec_supports_more_public_cloud_providers(tmp_path: Path) -> None:
    config_path = tmp_path / "linode.yaml"
    _write_yaml(
        config_path,
        {
            "name": "linode-campaign",
            "cloud": {"visibility": "public", "provider": "linode"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
        },
    )

    spec = load_spec(config_path)
    assert spec.cloud.provider == "linode"
    assert spec.kubernetes.distribution == "lke"


def test_load_spec_private_provider_validation(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-private.yaml"
    _write_yaml(
        config_path,
        {
            "name": "onprem-lab",
            "cloud": {"visibility": "private", "provider": "aws"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
        },
    )

    with pytest.raises(ValueError, match="not supported for private cloud"):
        load_spec(config_path)


def test_load_spec_requires_openclaw_base_url(tmp_path: Path) -> None:
    config_path = tmp_path / "missing-openclaw.yaml"
    _write_yaml(
        config_path,
        {
            "name": "onprem-lab",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {},
        },
    )

    with pytest.raises(ValueError, match="base_url"):
        load_spec(config_path)


def test_load_spec_private_kubernetes_cluster_options(tmp_path: Path) -> None:
    config_path = tmp_path / "private-k8s.yaml"
    _write_yaml(
        config_path,
        {
            "name": "private-k8s",
            "cloud": {"visibility": "private", "provider": "vmware"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {"orchestrator": "kubernetes"},
            "kubernetes": {
                "distribution": "rke2",
                "service_type": "LoadBalancer",
                "create_network_policy": True,
                "namespace_annotations": {"environment": "lab"},
            },
        },
    )

    spec = load_spec(config_path)
    assert spec.runtime.orchestrator == "kubernetes"
    assert spec.kubernetes.distribution == "rke2"
    assert spec.kubernetes.service_type == "LoadBalancer"
    assert spec.kubernetes.create_network_policy is True
    assert spec.kubernetes.namespace_annotations["environment"] == "lab"


def test_load_spec_rejects_invalid_private_kubernetes_distribution(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bad-private-k8s.yaml"
    _write_yaml(
        config_path,
        {
            "name": "private-k8s",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {"orchestrator": "kubernetes"},
            "kubernetes": {"distribution": "eks"},
        },
    )

    with pytest.raises(ValueError, match="not valid for private cloud Kubernetes"):
        load_spec(config_path)


def test_load_spec_gpu_required_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "gpu-required.yaml"
    _write_yaml(
        config_path,
        {
            "name": "gpu-required",
            "cloud": {"visibility": "public", "provider": "gcp"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
            "gpu": {
                "mode": "required",
                "vendor": "nvidia",
                "count": 2,
                "resource_name": "nvidia.com/gpu",
                "mcp_enabled": True,
                "campaign_enabled": True,
                "node_selector": {"nodepool": "gpu"},
                "toleration_key": "nvidia.com/gpu",
            },
        },
    )

    spec = load_spec(config_path)
    assert spec.gpu.mode == "required"
    assert spec.gpu.count == 2
    assert spec.gpu.node_selector["nodepool"] == "gpu"
    assert spec.gpu.campaign_enabled is True


def test_load_spec_gpu_off_disables_gpu_workloads(tmp_path: Path) -> None:
    config_path = tmp_path / "gpu-off.yaml"
    _write_yaml(
        config_path,
        {
            "name": "gpu-off",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "gpu": {
                "mode": "off",
                "mcp_enabled": True,
                "campaign_enabled": True,
            },
        },
    )

    spec = load_spec(config_path)
    assert spec.gpu.mode == "off"
    assert spec.gpu.mcp_enabled is False
    assert spec.gpu.campaign_enabled is False


def test_load_spec_supports_explicit_mcp_service_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp-service.yaml"
    _write_yaml(
        config_path,
        {
            "name": "mcp-service",
            "cloud": {"visibility": "public", "provider": "aws"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
            "runtime": {
                "orchestrator": "kubernetes",
                "mcp": {"mode": "service"},
            },
        },
    )

    spec = load_spec(config_path)
    assert spec.runtime.mcp.mode == "service"


def test_load_spec_supports_single_machine_orchestrator(tmp_path: Path) -> None:
    config_path = tmp_path / "single-machine.yaml"
    _write_yaml(
        config_path,
        {
            "name": "single-machine",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {
                "orchestrator": "single-machine",
            },
        },
    )

    spec = load_spec(config_path)
    assert spec.runtime.orchestrator == "single-machine"
