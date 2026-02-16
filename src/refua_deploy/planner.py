from __future__ import annotations

from typing import Any

from refua_deploy.autodetect import resolve_automation
from refua_deploy.integration import WorkspaceIntegration, integration_payload, resolve_images
from refua_deploy.models import DeploymentSpec


def build_plan(spec: DeploymentSpec, workspace: WorkspaceIntegration) -> dict[str, Any]:
    campaign_image, mcp_image = resolve_images(spec, workspace)
    resolved = resolve_automation(spec)
    artifacts = _artifact_list(spec, ingress_host=resolved.ingress_host)

    return {
        "name": spec.name,
        "cloud": {
            "visibility": spec.cloud.visibility,
            "provider": spec.cloud.provider,
            "region": spec.cloud.region,
        },
        "runtime": {
            "namespace": spec.runtime.namespace,
            "orchestrator": spec.runtime.orchestrator,
            "campaign": {
                "image": campaign_image,
                "objective": spec.runtime.campaign.objective,
                "schedule": spec.runtime.campaign.schedule,
                "max_rounds": spec.runtime.campaign.max_rounds,
                "max_calls": spec.runtime.campaign.max_calls,
                "output_path": spec.runtime.campaign.output_path,
            },
            "mcp": {
                "image": mcp_image,
                "replicas": spec.runtime.mcp.replicas,
                "port": spec.runtime.mcp.port,
                "transport": spec.runtime.mcp.transport,
            },
        },
        "kubernetes": {
            "distribution": spec.kubernetes.distribution,
            "ingress_class": spec.kubernetes.ingress_class,
            "service_type": spec.kubernetes.service_type,
            "storage_class": spec.kubernetes.storage_class,
            "create_network_policy": spec.kubernetes.create_network_policy,
            "namespace_annotations": dict(spec.kubernetes.namespace_annotations),
        },
        "gpu": {
            "mode": spec.gpu.mode,
            "vendor": spec.gpu.vendor,
            "count": spec.gpu.count,
            "resource_name": spec.gpu.resource_name,
            "mcp_enabled": spec.gpu.mcp_enabled,
            "campaign_enabled": spec.gpu.campaign_enabled,
            "node_selector": dict(spec.gpu.node_selector),
            "toleration_key": spec.gpu.toleration_key,
        },
        "automation": {
            "auto_discover_network": spec.automation.auto_discover_network,
            "bootstrap_cluster": spec.automation.bootstrap_cluster,
            "provisioning_level": spec.automation.provisioning_level,
            "cluster_name": resolved.cluster_name,
            "kubernetes_version": spec.automation.kubernetes_version,
            "node_count": spec.automation.node_count,
            "node_instance_type": resolved.node_instance_type,
            "node_disk_gb": spec.automation.node_disk_gb,
        },
        "openclaw": {
            "base_url": spec.openclaw.base_url,
            "model": spec.openclaw.model,
            "timeout_seconds": spec.openclaw.timeout_seconds,
            "token_secret_name": spec.openclaw.token_secret_name,
            "token_secret_key": spec.openclaw.token_secret_key,
        },
        "network": {
            "expose_mcp": spec.network.expose_mcp,
            "ingress_host": resolved.ingress_host,
            "allowed_hosts": list(resolved.allowed_hosts),
            "allowed_origins": list(resolved.allowed_origins),
        },
        "security": {
            "mcp_auth_secret_name": spec.security.mcp_auth_secret_name,
            "mcp_auth_secret_key": spec.security.mcp_auth_secret_key,
            "create_placeholder_secrets": spec.security.create_placeholder_secrets,
        },
        "storage": {
            "output_volume_size": spec.storage.output_volume_size,
        },
        "metadata": resolved.metadata,
        "integration": integration_payload(workspace),
        "artifacts": artifacts,
    }


def _artifact_list(spec: DeploymentSpec, *, ingress_host: str | None) -> list[str]:
    if spec.uses_kubernetes:
        artifacts = [
            "kubernetes/namespace.yaml",
            "kubernetes/configmap.yaml",
            "kubernetes/secrets.template.yaml",
            "kubernetes/campaign-output-pvc.yaml",
            "kubernetes/mcp-deployment.yaml",
            "kubernetes/mcp-service.yaml",
            "kubernetes/campaign-cronjob.yaml",
            "kubernetes/kustomization.yaml",
        ]
        if spec.kubernetes.create_network_policy:
            artifacts.append("kubernetes/network-policy.yaml")
        if spec.network.expose_mcp and ingress_host:
            artifacts.append("kubernetes/mcp-ingress.yaml")
        if spec.automation.bootstrap_cluster:
            artifacts.extend(
                [
                    "bootstrap/metadata.auto.json",
                    "bootstrap/network.auto.env",
                    "bootstrap/cluster-bootstrap.sh",
                ]
            )
        return artifacts

    return [
        "private/docker-compose.yaml",
        "private/.env.template",
    ]
