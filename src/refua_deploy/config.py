from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from refua_deploy.models import (
    AutomationSettings,
    CampaignSettings,
    CloudTarget,
    DeploymentSpec,
    GpuMode,
    GpuSettings,
    GpuVendor,
    KubernetesDistribution,
    KubernetesServiceType,
    McpMode,
    KubernetesSettings,
    McpSettings,
    NetworkSettings,
    OpenClawSettings,
    OrchestratorType,
    RuntimeSettings,
    SecuritySettings,
    StorageSettings,
)

PUBLIC_PROVIDERS = {
    "aws",
    "gcp",
    "azure",
    "oci",
    "digitalocean",
    "linode",
    "vultr",
    "hetzner",
    "ibm",
    "alibaba",
    "scaleway",
    "exoscale",
}
PRIVATE_PROVIDERS = {
    "onprem",
    "openstack",
    "vmware",
    "baremetal",
    "proxmox",
    "nutanix",
}
SUPPORTED_ORCHESTRATORS = {"kubernetes", "compose"}
KUBERNETES_DISTRIBUTIONS = {
    "generic",
    "eks",
    "gke",
    "aks",
    "oke",
    "doks",
    "lke",
    "vke",
    "hke",
    "iks",
    "ack",
    "ske",
    "k3s",
    "rke2",
    "openshift",
    "talos",
    "kubeadm",
}
PUBLIC_KUBERNETES_DISTRIBUTIONS = {
    "generic",
    "eks",
    "gke",
    "aks",
    "oke",
    "doks",
    "lke",
    "vke",
    "hke",
    "iks",
    "ack",
    "ske",
}
PRIVATE_KUBERNETES_DISTRIBUTIONS = {
    "generic",
    "k3s",
    "rke2",
    "openshift",
    "talos",
    "kubeadm",
}
_SERVICE_TYPE_NORMALIZATION = {
    "clusterip": "ClusterIP",
    "nodeport": "NodePort",
    "loadbalancer": "LoadBalancer",
}
_GPU_MODE_VALUES = {"off", "auto", "required"}
_GPU_VENDOR_VALUES = {"nvidia", "amd", "intel"}
_GPU_RESOURCE_BY_VENDOR = {
    "nvidia": "nvidia.com/gpu",
    "amd": "amd.com/gpu",
    "intel": "gpu.intel.com/i915",
}
_MCP_MODE_VALUES = {"inprocess", "service"}
_DEFAULT_K8S_DISTRIBUTION_BY_PROVIDER = {
    "aws": "eks",
    "gcp": "gke",
    "azure": "aks",
    "oci": "oke",
    "digitalocean": "doks",
    "linode": "lke",
    "vultr": "vke",
    "hetzner": "hke",
    "ibm": "iks",
    "alibaba": "ack",
    "scaleway": "ske",
    "exoscale": "generic",
    "onprem": "k3s",
    "openstack": "kubeadm",
    "vmware": "rke2",
    "baremetal": "kubeadm",
    "proxmox": "k3s",
    "nutanix": "k3s",
}
_VALID_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

SUPPORTED_GPU_MODES = set(_GPU_MODE_VALUES)
SUPPORTED_GPU_VENDORS = set(_GPU_VENDOR_VALUES)
SUPPORTED_PROVISIONING_LEVELS = {"manual", "assisted", "auto"}


def load_data_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    raw = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(raw)
    else:
        parsed = yaml.safe_load(raw)
        data = {} if parsed is None else parsed

    if not isinstance(data, dict):
        raise ValueError(f"Top-level data in {file_path} must be a mapping/object")
    return data


def load_spec(path: str | Path) -> DeploymentSpec:
    payload = load_data_file(path)
    return spec_from_mapping(payload)


def spec_from_mapping(data: Mapping[str, Any]) -> DeploymentSpec:
    name = _required_str(data, "name")

    cloud_raw = _required_mapping(data, "cloud")
    visibility_raw = _required_str(cloud_raw, "visibility").lower()
    provider = _required_str(cloud_raw, "provider").lower()
    region = _optional_str(cloud_raw, "region")

    if visibility_raw not in {"public", "private"}:
        raise ValueError("cloud.visibility must be either 'public' or 'private'")
    visibility = cast(Literal["public", "private"], visibility_raw)
    if visibility == "public" and provider not in PUBLIC_PROVIDERS:
        allowed = ", ".join(sorted(PUBLIC_PROVIDERS))
        raise ValueError(
            f"cloud.provider '{provider}' is not supported for public cloud. Allowed: {allowed}"
        )
    if visibility == "private" and provider not in PRIVATE_PROVIDERS:
        allowed = ", ".join(sorted(PRIVATE_PROVIDERS))
        raise ValueError(
            f"cloud.provider '{provider}' is not supported for private cloud. Allowed: {allowed}"
        )

    openclaw_raw = _required_mapping(data, "openclaw")
    openclaw_base_url = _required_str(openclaw_raw, "base_url")
    openclaw_model = _optional_str(openclaw_raw, "model") or "openclaw:main"
    openclaw_timeout = _int(openclaw_raw.get("timeout_seconds", 180), "openclaw.timeout_seconds")
    openclaw_token_secret_name = (
        _optional_str(openclaw_raw, "token_secret_name") or "openclaw-gateway-token"
    )
    openclaw_token_secret_key = (
        _optional_str(openclaw_raw, "token_secret_key") or "OPENCLAW_GATEWAY_TOKEN"
    )

    runtime_raw = data.get("runtime", {})
    if runtime_raw is None:
        runtime_raw = {}
    if not isinstance(runtime_raw, Mapping):
        raise ValueError("runtime must be a mapping if provided")
    namespace = _optional_str(runtime_raw, "namespace") or _default_namespace(name)
    _validate_k8s_name(namespace, "runtime.namespace")

    default_orchestrator = "kubernetes" if visibility == "public" else "compose"
    orchestrator_raw = (
        _optional_str(runtime_raw, "orchestrator") or default_orchestrator
    ).lower()
    if orchestrator_raw not in SUPPORTED_ORCHESTRATORS:
        allowed = ", ".join(sorted(SUPPORTED_ORCHESTRATORS))
        raise ValueError(f"runtime.orchestrator must be one of: {allowed}")
    orchestrator = cast(OrchestratorType, orchestrator_raw)

    campaign_raw = runtime_raw.get("campaign", {})
    if campaign_raw is None:
        campaign_raw = {}
    if not isinstance(campaign_raw, Mapping):
        raise ValueError("runtime.campaign must be a mapping if provided")

    campaign = CampaignSettings(
        image=_optional_str(campaign_raw, "image"),
        objective=_optional_str(campaign_raw, "objective")
        or f"Run a Refua campaign for '{name}'",
        schedule=_optional_str(campaign_raw, "schedule") or "0 */6 * * *",
        max_rounds=_int(campaign_raw.get("max_rounds", 4), "runtime.campaign.max_rounds"),
        max_calls=_int(campaign_raw.get("max_calls", 12), "runtime.campaign.max_calls"),
        output_path=_optional_str(campaign_raw, "output_path")
        or "/var/lib/refua/output/latest_run.json",
    )

    mcp_raw = runtime_raw.get("mcp", {})
    if mcp_raw is None:
        mcp_raw = {}
    if not isinstance(mcp_raw, Mapping):
        raise ValueError("runtime.mcp must be a mapping if provided")

    mcp = McpSettings(
        mode=_parse_mcp_mode(_optional_str(mcp_raw, "mode") or "inprocess"),
        image=_optional_str(mcp_raw, "image"),
        replicas=_int(mcp_raw.get("replicas", 1), "runtime.mcp.replicas"),
        port=_int(mcp_raw.get("port", 8000), "runtime.mcp.port"),
        transport=_optional_str(mcp_raw, "transport") or "streamable-http",
    )
    if mcp.replicas < 1:
        raise ValueError("runtime.mcp.replicas must be >= 1")
    if mcp.port <= 0:
        raise ValueError("runtime.mcp.port must be > 0")

    kubernetes_raw = data.get("kubernetes", {})
    if kubernetes_raw is None:
        kubernetes_raw = {}
    if not isinstance(kubernetes_raw, Mapping):
        raise ValueError("kubernetes must be a mapping if provided")

    distribution_raw = (
        _optional_str(kubernetes_raw, "distribution")
        or _default_kubernetes_distribution(provider=provider, visibility=visibility)
    )
    distribution = _parse_distribution(
        value=distribution_raw,
        visibility=visibility,
        orchestrator=orchestrator,
    )

    service_type_raw = _optional_str(kubernetes_raw, "service_type") or "ClusterIP"
    service_type = _parse_service_type(service_type_raw)

    kubernetes = KubernetesSettings(
        distribution=distribution,
        ingress_class=_optional_str(kubernetes_raw, "ingress_class"),
        service_type=service_type,
        storage_class=_optional_str(kubernetes_raw, "storage_class"),
        create_network_policy=_bool(
            kubernetes_raw.get("create_network_policy", False),
            "kubernetes.create_network_policy",
        ),
        namespace_annotations=_str_mapping(
            kubernetes_raw.get("namespace_annotations", {}),
            "kubernetes.namespace_annotations",
        ),
    )

    gpu_raw = data.get("gpu", {})
    if gpu_raw is None:
        gpu_raw = {}
    if not isinstance(gpu_raw, Mapping):
        raise ValueError("gpu must be a mapping if provided")

    gpu_mode = _parse_gpu_mode(_optional_str(gpu_raw, "mode") or "auto")
    gpu_vendor = _parse_gpu_vendor(_optional_str(gpu_raw, "vendor") or "nvidia")
    gpu_count = _int(gpu_raw.get("count", 1), "gpu.count")
    if gpu_count < 1:
        raise ValueError("gpu.count must be >= 1")

    default_resource_name = _GPU_RESOURCE_BY_VENDOR[gpu_vendor]
    gpu_resource_name = _optional_str(gpu_raw, "resource_name") or default_resource_name
    gpu_node_selector = _str_mapping(gpu_raw.get("node_selector", {}), "gpu.node_selector")
    gpu_toleration_key = _optional_str(gpu_raw, "toleration_key")
    if gpu_toleration_key is None and gpu_mode != "off":
        gpu_toleration_key = gpu_resource_name

    if gpu_mode == "off":
        gpu_mcp_enabled = False
        gpu_campaign_enabled = False
    else:
        gpu_mcp_enabled = _bool(gpu_raw.get("mcp_enabled", True), "gpu.mcp_enabled")
        gpu_campaign_enabled = _bool(
            gpu_raw.get("campaign_enabled", False),
            "gpu.campaign_enabled",
        )

    gpu = GpuSettings(
        mode=gpu_mode,
        vendor=gpu_vendor,
        count=gpu_count,
        resource_name=gpu_resource_name,
        mcp_enabled=gpu_mcp_enabled,
        campaign_enabled=gpu_campaign_enabled,
        node_selector=gpu_node_selector,
        toleration_key=gpu_toleration_key,
    )

    automation_raw = data.get("automation", {})
    if automation_raw is None:
        automation_raw = {}
    if not isinstance(automation_raw, Mapping):
        raise ValueError("automation must be a mapping if provided")

    provisioning_level_raw = (
        _optional_str(automation_raw, "provisioning_level") or "auto"
    ).lower()
    if provisioning_level_raw not in SUPPORTED_PROVISIONING_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_PROVISIONING_LEVELS))
        raise ValueError(f"automation.provisioning_level must be one of: {allowed}")

    automation = AutomationSettings(
        auto_discover_network=_bool(
            automation_raw.get("auto_discover_network", True),
            "automation.auto_discover_network",
        ),
        bootstrap_cluster=_bool(
            automation_raw.get("bootstrap_cluster", True),
            "automation.bootstrap_cluster",
        ),
        provisioning_level=cast(
            Literal["manual", "assisted", "auto"],
            provisioning_level_raw,
        ),
        cluster_name=_optional_str(automation_raw, "cluster_name"),
        kubernetes_version=_optional_str(automation_raw, "kubernetes_version") or "1.30",
        node_count=_int(automation_raw.get("node_count", 3), "automation.node_count"),
        node_instance_type=_optional_str(automation_raw, "node_instance_type"),
        node_disk_gb=_int(automation_raw.get("node_disk_gb", 100), "automation.node_disk_gb"),
    )
    if automation.node_count < 1:
        raise ValueError("automation.node_count must be >= 1")
    if automation.node_disk_gb < 20:
        raise ValueError("automation.node_disk_gb must be >= 20")

    security_raw = data.get("security", {})
    if security_raw is None:
        security_raw = {}
    if not isinstance(security_raw, Mapping):
        raise ValueError("security must be a mapping if provided")

    security = SecuritySettings(
        mcp_auth_secret_name=_optional_str(security_raw, "mcp_auth_secret_name")
        or "refua-mcp-auth-token",
        mcp_auth_secret_key=_optional_str(security_raw, "mcp_auth_secret_key")
        or "REFUA_MCP_AUTH_TOKENS",
        create_placeholder_secrets=_bool(
            security_raw.get("create_placeholder_secrets", True),
            "security.create_placeholder_secrets",
        ),
    )
    _validate_k8s_name(security.mcp_auth_secret_name, "security.mcp_auth_secret_name")

    network_raw = data.get("network", {})
    if network_raw is None:
        network_raw = {}
    if not isinstance(network_raw, Mapping):
        raise ValueError("network must be a mapping if provided")

    network = NetworkSettings(
        expose_mcp=_bool(network_raw.get("expose_mcp", True), "network.expose_mcp"),
        ingress_host=_optional_str(network_raw, "ingress_host"),
        allowed_hosts=_str_list(network_raw.get("allowed_hosts", []), "network.allowed_hosts"),
        allowed_origins=_str_list(
            network_raw.get("allowed_origins", []),
            "network.allowed_origins",
        ),
    )

    storage_raw = data.get("storage", {})
    if storage_raw is None:
        storage_raw = {}
    if not isinstance(storage_raw, Mapping):
        raise ValueError("storage must be a mapping if provided")

    storage = StorageSettings(
        output_volume_size=_optional_str(storage_raw, "output_volume_size") or "20Gi",
    )

    return DeploymentSpec(
        name=name,
        cloud=CloudTarget(visibility=visibility, provider=provider, region=region),
        openclaw=OpenClawSettings(
            base_url=openclaw_base_url,
            model=openclaw_model,
            timeout_seconds=openclaw_timeout,
            token_secret_name=openclaw_token_secret_name,
            token_secret_key=openclaw_token_secret_key,
        ),
        runtime=RuntimeSettings(
            namespace=namespace,
            orchestrator=orchestrator,
            campaign=campaign,
            mcp=mcp,
        ),
        kubernetes=kubernetes,
        gpu=gpu,
        automation=automation,
        security=security,
        network=network,
        storage=storage,
    )


def starter_mapping(
    *,
    name: str,
    visibility: str,
    provider: str,
    campaign_image: str,
    mcp_image: str,
    orchestrator: str | None = None,
    gpu_mode: str | None = None,
    gpu_vendor: str | None = None,
    provisioning_level: str = "auto",
) -> dict[str, Any]:
    _validate_k8s_name(_default_namespace(name), "generated namespace")
    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be either 'public' or 'private'")
    visibility_value = cast(Literal["public", "private"], visibility)

    resolved_orchestrator = orchestrator or (
        "kubernetes" if visibility_value == "public" else "compose"
    )
    resolved_gpu_mode = _parse_gpu_mode(gpu_mode or "auto")
    resolved_gpu_vendor = _parse_gpu_vendor(gpu_vendor or "nvidia")
    resolved_gpu_resource = _GPU_RESOURCE_BY_VENDOR[resolved_gpu_vendor]
    normalized_provisioning_level = provisioning_level.strip().lower()
    if normalized_provisioning_level not in SUPPORTED_PROVISIONING_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_PROVISIONING_LEVELS))
        raise ValueError(f"provisioning_level must be one of: {allowed}")
    distribution = _default_kubernetes_distribution(
        provider=provider,
        visibility=visibility_value,
    )

    return {
        "name": name,
        "cloud": {
            "visibility": visibility,
            "provider": provider,
            "region": "us-east-1" if visibility_value == "public" else "dc1",
        },
        "openclaw": {
            "base_url": "https://openclaw.example.org",
            "model": "openclaw:main",
            "timeout_seconds": 180,
            "token_secret_name": "openclaw-gateway-token",
            "token_secret_key": "OPENCLAW_GATEWAY_TOKEN",
        },
        "runtime": {
            "namespace": _default_namespace(name),
            "orchestrator": resolved_orchestrator,
            "campaign": {
                "image": campaign_image,
                "objective": (
                    "Design and execute a Refua campaign for high-priority disease targets"
                ),
                "schedule": "0 */6 * * *",
                "max_rounds": 4,
                "max_calls": 12,
                "output_path": "/var/lib/refua/output/latest_run.json",
            },
            "mcp": {
                "mode": "inprocess",
                "image": mcp_image,
                "replicas": 1,
                "port": 8000,
                "transport": "streamable-http",
            },
        },
        "kubernetes": {
            "distribution": distribution,
            "ingress_class": "nginx" if resolved_orchestrator == "kubernetes" else None,
            "service_type": "ClusterIP",
            "storage_class": None,
            "create_network_policy": False,
            "namespace_annotations": {},
        },
        "gpu": {
            "mode": resolved_gpu_mode,
            "vendor": resolved_gpu_vendor,
            "count": 1,
            "resource_name": resolved_gpu_resource,
            "mcp_enabled": resolved_gpu_mode != "off",
            "campaign_enabled": False,
            "node_selector": {},
            "toleration_key": resolved_gpu_resource if resolved_gpu_mode != "off" else None,
        },
        "automation": {
            "auto_discover_network": True,
            "bootstrap_cluster": True,
            "provisioning_level": normalized_provisioning_level,
            "cluster_name": None,
            "kubernetes_version": "1.30",
            "node_count": 3,
            "node_instance_type": None,
            "node_disk_gb": 100,
        },
        "security": {
            "mcp_auth_secret_name": "refua-mcp-auth-token",
            "mcp_auth_secret_key": "REFUA_MCP_AUTH_TOKENS",
            "create_placeholder_secrets": True,
        },
        "network": {
            "expose_mcp": True,
            "ingress_host": None,
            "allowed_hosts": [],
            "allowed_origins": [],
        },
        "storage": {
            "output_volume_size": "20Gi",
        },
    }


def dump_mapping_yaml(path: str | Path, payload: Mapping[str, Any]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(dict(payload), sort_keys=False), encoding="utf-8")


def _required_mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} is required and must be a mapping")
    return value


def _required_str(data: Mapping[str, Any], key: str) -> str:
    value = _optional_str(data, key)
    if value is None:
        raise ValueError(f"{key} is required and must be a non-empty string")
    return value


def _optional_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    value_str = str(value).strip()
    if not value_str:
        return None
    return value_str


def _int(value: Any, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a boolean")


def _str_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _str_mapping(value: Any, key: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping of string keys and values")

    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key_text = str(raw_key).strip()
        value_text = str(raw_value).strip()
        if not key_text:
            continue
        parsed[key_text] = value_text
    return parsed


def _parse_distribution(
    *,
    value: str,
    visibility: Literal["public", "private"],
    orchestrator: OrchestratorType,
) -> KubernetesDistribution:
    normalized = value.strip().lower()
    if normalized not in KUBERNETES_DISTRIBUTIONS:
        allowed = ", ".join(sorted(KUBERNETES_DISTRIBUTIONS))
        raise ValueError(f"kubernetes.distribution must be one of: {allowed}")

    if orchestrator == "kubernetes":
        allowed_for_visibility = (
            PUBLIC_KUBERNETES_DISTRIBUTIONS
            if visibility == "public"
            else PRIVATE_KUBERNETES_DISTRIBUTIONS
        )
        if normalized not in allowed_for_visibility:
            allowed = ", ".join(sorted(allowed_for_visibility))
            raise ValueError(
                f"kubernetes.distribution '{normalized}' is not valid for {visibility} cloud "
                f"Kubernetes. Allowed: {allowed}"
            )

    return cast(KubernetesDistribution, normalized)


def _parse_service_type(value: str) -> KubernetesServiceType:
    normalized = value.strip().lower()
    resolved = _SERVICE_TYPE_NORMALIZATION.get(normalized)
    if resolved is None:
        allowed = ", ".join(_SERVICE_TYPE_NORMALIZATION.values())
        raise ValueError(f"kubernetes.service_type must be one of: {allowed}")
    return cast(KubernetesServiceType, resolved)


def _parse_gpu_mode(value: str) -> GpuMode:
    normalized = value.strip().lower()
    if normalized not in _GPU_MODE_VALUES:
        allowed = ", ".join(sorted(_GPU_MODE_VALUES))
        raise ValueError(f"gpu.mode must be one of: {allowed}")
    return cast(GpuMode, normalized)


def _parse_gpu_vendor(value: str) -> GpuVendor:
    normalized = value.strip().lower()
    if normalized not in _GPU_VENDOR_VALUES:
        allowed = ", ".join(sorted(_GPU_VENDOR_VALUES))
        raise ValueError(f"gpu.vendor must be one of: {allowed}")
    return cast(GpuVendor, normalized)


def _parse_mcp_mode(value: str) -> McpMode:
    normalized = value.strip().lower()
    if normalized not in _MCP_MODE_VALUES:
        allowed = ", ".join(sorted(_MCP_MODE_VALUES))
        raise ValueError(f"runtime.mcp.mode must be one of: {allowed}")
    return cast(McpMode, normalized)


def _default_namespace(name: str) -> str:
    candidate = name.lower().replace("_", "-")
    candidate = re.sub(r"[^a-z0-9-]", "-", candidate)
    candidate = re.sub(r"-+", "-", candidate).strip("-")
    return candidate or "refua"


def _default_kubernetes_distribution(
    *,
    provider: str,
    visibility: Literal["public", "private"],
) -> str:
    resolved = _DEFAULT_K8S_DISTRIBUTION_BY_PROVIDER.get(provider)
    if resolved:
        return resolved
    return "generic" if visibility == "public" else "k3s"


def _validate_k8s_name(value: str, key: str) -> None:
    if not _VALID_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"{key} must be a DNS-1123 compatible name (lowercase alnum and '-')"
        )
