from __future__ import annotations

from refua_deploy.autodetect import resolve_automation
from refua_deploy.config import spec_from_mapping


def test_resolve_automation_infers_network_from_metadata_env() -> None:
    spec = spec_from_mapping(
        {
            "name": "autonet",
            "cloud": {"visibility": "public", "provider": "aws", "region": "us-east-1"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
            "runtime": {"orchestrator": "kubernetes"},
            "network": {"expose_mcp": True, "ingress_host": None},
        }
    )

    resolved = resolve_automation(
        spec,
        env={
            "REFUA_DEPLOY_ENABLE_METADATA_HTTP": "0",
            "REFUA_PUBLIC_IP": "203.0.113.10",
            "REFUA_PRIVATE_IP": "10.0.1.15",
            "REFUA_AWS_VPC_ID": "vpc-123",
            "REFUA_AWS_SUBNET_IDS": "subnet-a,subnet-b",
        },
    )

    assert resolved.ingress_host == "autonet.203.0.113.10.nip.io"
    assert "autonet-mcp.autonet.svc.cluster.local" in resolved.allowed_hosts
    assert "10.0.1.15" in resolved.allowed_hosts
    assert resolved.allowed_origins == ["https://autonet.203.0.113.10.nip.io"]
    assert resolved.cluster_name == "autonet-aws"
    assert resolved.node_instance_type == "g5.xlarge"
    assert resolved.metadata["vpc_id"] == "vpc-123"


def test_resolve_automation_defaults_compose_local_origins() -> None:
    spec = spec_from_mapping(
        {
            "name": "compose-auto",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {"orchestrator": "compose"},
            "network": {"expose_mcp": True},
        }
    )

    resolved = resolve_automation(
        spec,
        env={
            "REFUA_DEPLOY_ENABLE_METADATA_HTTP": "0",
        },
    )

    assert resolved.ingress_host is None
    assert "localhost" in resolved.allowed_hosts
    assert "127.0.0.1" in resolved.allowed_hosts
    assert any(
        item.startswith("http://localhost:") for item in resolved.allowed_origins
    )


def test_resolve_automation_defaults_single_machine_local_origins() -> None:
    spec = spec_from_mapping(
        {
            "name": "single-box",
            "cloud": {"visibility": "private", "provider": "onprem"},
            "openclaw": {"base_url": "https://openclaw.internal"},
            "runtime": {"orchestrator": "single-machine", "mcp": {"port": 9010}},
            "network": {"expose_mcp": True},
        }
    )

    resolved = resolve_automation(
        spec,
        env={
            "REFUA_DEPLOY_ENABLE_METADATA_HTTP": "0",
        },
    )

    assert resolved.ingress_host is None
    assert "localhost" in resolved.allowed_hosts
    assert "127.0.0.1" in resolved.allowed_hosts
    assert "single-box-mcp.single-box.svc.cluster.local" not in resolved.allowed_hosts
    assert "http://localhost:9010" in resolved.allowed_origins
