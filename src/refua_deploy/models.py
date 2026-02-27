from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CloudVisibility = Literal["public", "private"]
OrchestratorType = Literal["kubernetes", "compose", "single-machine"]
McpMode = Literal["inprocess", "service"]
KubernetesDistribution = Literal[
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
]
KubernetesServiceType = Literal["ClusterIP", "NodePort", "LoadBalancer"]
GpuMode = Literal["off", "auto", "required"]
GpuVendor = Literal["nvidia", "amd", "intel"]
ProvisioningLevel = Literal["manual", "assisted", "auto"]


@dataclass(slots=True)
class CloudTarget:
    visibility: CloudVisibility
    provider: str
    region: str | None = None


@dataclass(slots=True)
class OpenClawSettings:
    base_url: str
    model: str = "openclaw:main"
    timeout_seconds: int = 180
    token_secret_name: str = "openclaw-gateway-token"
    token_secret_key: str = "OPENCLAW_GATEWAY_TOKEN"


@dataclass(slots=True)
class CampaignSettings:
    image: str | None = None
    objective: str = "Design and execute a high-impact Refua campaign"
    schedule: str = "0 */6 * * *"
    max_rounds: int = 4
    max_calls: int = 12
    output_path: str = "/var/lib/refua/output/latest_run.json"


@dataclass(slots=True)
class McpSettings:
    mode: McpMode = "inprocess"
    image: str | None = None
    replicas: int = 1
    port: int = 8000
    transport: str = "streamable-http"


@dataclass(slots=True)
class RuntimeSettings:
    namespace: str = "refua"
    orchestrator: OrchestratorType = "kubernetes"
    campaign: CampaignSettings = field(default_factory=CampaignSettings)
    mcp: McpSettings = field(default_factory=McpSettings)


@dataclass(slots=True)
class SecuritySettings:
    mcp_auth_secret_name: str = "refua-mcp-auth-token"
    mcp_auth_secret_key: str = "REFUA_MCP_AUTH_TOKENS"
    create_placeholder_secrets: bool = True


@dataclass(slots=True)
class NetworkSettings:
    expose_mcp: bool = True
    ingress_host: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StorageSettings:
    output_volume_size: str = "20Gi"


@dataclass(slots=True)
class GpuSettings:
    mode: GpuMode = "auto"
    vendor: GpuVendor = "nvidia"
    count: int = 1
    resource_name: str = "nvidia.com/gpu"
    mcp_enabled: bool = True
    campaign_enabled: bool = False
    node_selector: dict[str, str] = field(default_factory=dict)
    toleration_key: str | None = "nvidia.com/gpu"


@dataclass(slots=True)
class AutomationSettings:
    auto_discover_network: bool = True
    bootstrap_cluster: bool = True
    provisioning_level: ProvisioningLevel = "auto"
    cluster_name: str | None = None
    kubernetes_version: str = "1.30"
    node_count: int = 3
    node_instance_type: str | None = None
    node_disk_gb: int = 100


@dataclass(slots=True)
class KubernetesSettings:
    distribution: KubernetesDistribution = "generic"
    ingress_class: str | None = None
    service_type: KubernetesServiceType = "ClusterIP"
    storage_class: str | None = None
    create_network_policy: bool = False
    namespace_annotations: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DeploymentSpec:
    name: str
    cloud: CloudTarget
    openclaw: OpenClawSettings
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    kubernetes: KubernetesSettings = field(default_factory=KubernetesSettings)
    gpu: GpuSettings = field(default_factory=GpuSettings)
    automation: AutomationSettings = field(default_factory=AutomationSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)

    @property
    def is_public(self) -> bool:
        return self.cloud.visibility == "public"

    @property
    def is_private(self) -> bool:
        return self.cloud.visibility == "private"

    @property
    def uses_kubernetes(self) -> bool:
        return self.runtime.orchestrator == "kubernetes"

    @property
    def uses_compose(self) -> bool:
        return self.runtime.orchestrator == "compose"

    @property
    def uses_single_machine(self) -> bool:
        return self.runtime.orchestrator == "single-machine"
