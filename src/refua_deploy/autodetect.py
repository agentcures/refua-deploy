from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from refua_deploy.models import DeploymentSpec

_METADATA_TIMEOUT_SECONDS = 0.15
_DEFAULT_NODE_TYPE_CPU = {
    "aws": "m6i.large",
    "gcp": "e2-standard-4",
    "azure": "Standard_D4s_v5",
    "oci": "VM.Standard.E4.Flex",
    "digitalocean": "s-4vcpu-8gb",
    "linode": "g6-standard-4",
    "vultr": "vc2-4c-8gb",
    "hetzner": "cx31",
    "ibm": "bx2-4x16",
    "alibaba": "ecs.c7.large",
    "scaleway": "DEV1-L",
    "exoscale": "standard.medium",
    "onprem": "cpu-node",
    "openstack": "m1.large",
    "vmware": "standard-medium",
    "baremetal": "cpu-node",
    "proxmox": "cpu-node",
    "nutanix": "cpu-node",
}
_DEFAULT_NODE_TYPE_GPU = {
    "aws": "g5.xlarge",
    "gcp": "g2-standard-4",
    "azure": "Standard_NC4as_T4_v3",
    "oci": "VM.GPU.A10.1",
    "digitalocean": "g-2vcpu-8gb",
    "linode": "g6-gpu-1",
    "vultr": "vcg-a16-2c-8g-40vram",
    "hetzner": "ccx23",
    "ibm": "gx2-8x64x1v100",
    "alibaba": "ecs.gn7i-c8g1.2xlarge",
    "scaleway": "gpu-3070-s",
    "exoscale": "gpu.medium",
    "onprem": "gpu-node",
    "openstack": "gpu.large",
    "vmware": "gpu-medium",
    "baremetal": "gpu-node",
    "proxmox": "gpu-node",
    "nutanix": "gpu-node",
}


@dataclass(slots=True)
class ResolvedAutomation:
    ingress_host: str | None
    allowed_hosts: list[str]
    allowed_origins: list[str]
    cluster_name: str
    node_instance_type: str
    metadata: dict[str, Any]


def resolve_automation(
    spec: DeploymentSpec,
    env: Mapping[str, str] | None = None,
) -> ResolvedAutomation:
    env_map = dict(os.environ if env is None else env)

    metadata = _collect_metadata(
        provider=spec.cloud.provider,
        env=env_map,
        allow_http=(
            spec.automation.auto_discover_network
            and spec.automation.provisioning_level != "manual"
        ),
    )
    metadata.setdefault("provider", spec.cloud.provider)
    metadata.setdefault("region", spec.cloud.region)

    ingress_host = _resolve_ingress_host(spec=spec, metadata=metadata, env=env_map)

    inferred_hosts: list[str] = []
    if spec.uses_kubernetes:
        service_dns = (
            f"{spec.runtime.namespace}-mcp.{spec.runtime.namespace}.svc.cluster.local"
        )
        inferred_hosts.append(service_dns)
    if ingress_host:
        inferred_hosts.append(ingress_host)
    private_ip = _first_non_empty(metadata, ["private_ip", "local_ip"])
    if private_ip:
        inferred_hosts.append(private_ip)
    if spec.uses_compose or spec.uses_single_machine:
        inferred_hosts.extend(["127.0.0.1", "localhost"])

    allowed_hosts = _merge_with_defaults(
        explicit=spec.network.allowed_hosts,
        inferred=inferred_hosts,
    )

    inferred_origins: list[str] = []
    if ingress_host:
        inferred_origins.append(f"https://{ingress_host}")
    if spec.uses_compose or spec.uses_single_machine:
        exposed_port = str(spec.runtime.mcp.port)
        inferred_origins.extend(
            [
                f"http://localhost:{exposed_port}",
                f"http://127.0.0.1:{exposed_port}",
            ]
        )
    allowed_origins = _merge_with_defaults(
        explicit=spec.network.allowed_origins,
        inferred=inferred_origins,
    )

    cluster_name = _sanitize_cluster_name(
        spec.automation.cluster_name
        or f"{spec.runtime.namespace}-{spec.cloud.provider}"
    )

    needs_gpu = spec.gpu.mode != "off" and (
        spec.gpu.mcp_enabled or spec.gpu.campaign_enabled
    )
    node_instance_type = (
        spec.automation.node_instance_type
        or _default_node_instance_type(
            provider=spec.cloud.provider,
            needs_gpu=needs_gpu,
        )
    )

    metadata["resolved_cluster_name"] = cluster_name
    metadata["resolved_node_instance_type"] = node_instance_type
    metadata["resolved_ingress_host"] = ingress_host

    return ResolvedAutomation(
        ingress_host=ingress_host,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        cluster_name=cluster_name,
        node_instance_type=node_instance_type,
        metadata=metadata,
    )


def _resolve_ingress_host(
    *,
    spec: DeploymentSpec,
    metadata: Mapping[str, Any],
    env: Mapping[str, str],
) -> str | None:
    explicit = spec.network.ingress_host
    if explicit:
        return explicit

    env_host = _first_non_empty(
        env, ["REFUA_INGRESS_HOST", "REFUA_DEPLOY_INGRESS_HOST"]
    )
    if env_host:
        return env_host

    if not spec.network.expose_mcp or not spec.uses_kubernetes:
        return None

    public_hostname = _first_non_empty(metadata, ["public_hostname", "public_dns"])
    if public_hostname:
        return public_hostname

    public_ip = _first_non_empty(metadata, ["public_ip", "external_ip"])
    if public_ip and _is_ipv4(public_ip):
        return f"{spec.runtime.namespace}.{public_ip}.nip.io"

    region = str(metadata.get("region") or spec.cloud.region or "global")
    if spec.is_public:
        return f"{spec.runtime.namespace}.{region}.{spec.cloud.provider}.refua.run"
    return f"{spec.runtime.namespace}.local"


def _collect_metadata(
    *,
    provider: str,
    env: Mapping[str, str],
    allow_http: bool,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "defaults",
        "provider": provider,
    }

    env_metadata = _collect_env_metadata(provider=provider, env=env)
    metadata.update(env_metadata)

    if allow_http and _metadata_http_enabled(env):
        http_metadata: dict[str, Any] = {}
        if provider == "aws":
            http_metadata = _collect_aws_metadata()
        elif provider == "gcp":
            http_metadata = _collect_gcp_metadata()
        elif provider == "azure":
            http_metadata = _collect_azure_metadata()

        for key, value in http_metadata.items():
            if key not in metadata or _is_empty(metadata[key]):
                metadata[key] = value

        if http_metadata:
            metadata["source"] = "mixed" if env_metadata else "metadata"
        elif env_metadata:
            metadata["source"] = "env"
    elif env_metadata:
        metadata["source"] = "env"

    return metadata


def _collect_env_metadata(provider: str, env: Mapping[str, str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    metadata["region"] = _first_non_empty(
        env,
        [
            "REFUA_CLOUD_REGION",
            "REFUA_DEPLOY_REGION",
            "AWS_REGION",
            "GOOGLE_CLOUD_REGION",
            "AZURE_REGION",
        ],
    )
    metadata["public_ip"] = _first_non_empty(env, ["REFUA_PUBLIC_IP", "PUBLIC_IP"])
    metadata["private_ip"] = _first_non_empty(env, ["REFUA_PRIVATE_IP", "PRIVATE_IP"])
    metadata["public_hostname"] = _first_non_empty(
        env,
        ["REFUA_PUBLIC_HOSTNAME", "PUBLIC_HOSTNAME"],
    )

    if provider == "aws":
        metadata["vpc_id"] = _first_non_empty(env, ["REFUA_AWS_VPC_ID", "AWS_VPC_ID"])
        metadata["subnet_ids"] = _split_csv(
            _first_non_empty(env, ["REFUA_AWS_SUBNET_IDS", "AWS_SUBNET_IDS"])
        )
    elif provider == "gcp":
        metadata["project_id"] = _first_non_empty(
            env, ["GOOGLE_CLOUD_PROJECT", "GCP_PROJECT_ID"]
        )
        metadata["network"] = _first_non_empty(
            env, ["REFUA_GCP_NETWORK", "GCP_NETWORK"]
        )
        metadata["subnetwork"] = _first_non_empty(
            env,
            ["REFUA_GCP_SUBNETWORK", "GCP_SUBNETWORK"],
        )
    elif provider == "azure":
        metadata["resource_group"] = _first_non_empty(
            env,
            ["REFUA_AZURE_RESOURCE_GROUP", "AZURE_RESOURCE_GROUP"],
        )
        metadata["vnet"] = _first_non_empty(env, ["REFUA_AZURE_VNET", "AZURE_VNET"])
        metadata["subnet_ids"] = _split_csv(
            _first_non_empty(env, ["REFUA_AZURE_SUBNET_IDS", "AZURE_SUBNET_IDS"])
        )

    return {k: v for k, v in metadata.items() if not _is_empty(v)}


def _collect_aws_metadata() -> dict[str, Any]:
    token = _http_text(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    if not token:
        return {}

    auth_headers = {"X-aws-ec2-metadata-token": token}
    region = _http_text(
        "http://169.254.169.254/latest/meta-data/placement/region",
        headers=auth_headers,
    )
    if not region:
        az = _http_text(
            "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            headers=auth_headers,
        )
        region = az[:-1] if az and len(az) > 1 else None

    local_ip = _http_text(
        "http://169.254.169.254/latest/meta-data/local-ipv4",
        headers=auth_headers,
    )
    public_ip = _http_text(
        "http://169.254.169.254/latest/meta-data/public-ipv4",
        headers=auth_headers,
    )

    metadata: dict[str, Any] = {
        "region": region,
        "private_ip": local_ip,
        "public_ip": public_ip,
    }

    macs = _http_text(
        "http://169.254.169.254/latest/meta-data/network/interfaces/macs/",
        headers=auth_headers,
    )
    if macs:
        first_mac = macs.splitlines()[0].strip().strip("/")
        if first_mac:
            vpc_id = _http_text(
                f"http://169.254.169.254/latest/meta-data/network/interfaces/macs/{first_mac}/vpc-id",
                headers=auth_headers,
            )
            subnet_id = _http_text(
                f"http://169.254.169.254/latest/meta-data/network/interfaces/macs/{first_mac}/subnet-id",
                headers=auth_headers,
            )
            if vpc_id:
                metadata["vpc_id"] = vpc_id
            if subnet_id:
                metadata["subnet_ids"] = [subnet_id]

    return {k: v for k, v in metadata.items() if v not in {None, ""}}


def _collect_gcp_metadata() -> dict[str, Any]:
    headers = {"Metadata-Flavor": "Google"}
    base = "http://metadata.google.internal/computeMetadata/v1"

    zone = _http_text(f"{base}/instance/zone", headers=headers)
    region = None
    if zone and "/" in zone:
        zone = zone.rsplit("/", 1)[-1]
        if "-" in zone:
            region = zone.rsplit("-", 1)[0]

    network = _http_text(
        f"{base}/instance/network-interfaces/0/network", headers=headers
    )
    if network and "/" in network:
        network = network.rsplit("/", 1)[-1]

    subnetwork = _http_text(
        f"{base}/instance/network-interfaces/0/subnetwork",
        headers=headers,
    )
    if subnetwork and "/" in subnetwork:
        subnetwork = subnetwork.rsplit("/", 1)[-1]

    private_ip = _http_text(f"{base}/instance/network-interfaces/0/ip", headers=headers)
    public_ip = _http_text(
        f"{base}/instance/network-interfaces/0/access-configs/0/external-ip",
        headers=headers,
    )

    metadata = {
        "region": region,
        "network": network,
        "subnetwork": subnetwork,
        "private_ip": private_ip,
        "public_ip": public_ip,
    }
    return {k: v for k, v in metadata.items() if v not in {None, ""}}


def _collect_azure_metadata() -> dict[str, Any]:
    headers = {"Metadata": "true"}
    compute_raw = _http_text(
        "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01&format=json",
        headers=headers,
    )
    network_raw = _http_text(
        "http://169.254.169.254/metadata/instance/network/interface?api-version=2021-02-01&format=json",
        headers=headers,
    )

    metadata: dict[str, Any] = {}
    if compute_raw:
        try:
            compute_payload = json.loads(compute_raw)
            if isinstance(compute_payload, dict):
                metadata["region"] = compute_payload.get("location")
                metadata["resource_group"] = compute_payload.get("resourceGroupName")
        except json.JSONDecodeError:
            pass

    if network_raw:
        try:
            network_payload = json.loads(network_raw)
            if isinstance(network_payload, list) and network_payload:
                first_iface = network_payload[0]
            elif isinstance(network_payload, dict):
                first_iface = network_payload
            else:
                first_iface = {}

            ipv4_payload = (
                first_iface.get("ipv4") if isinstance(first_iface, dict) else None
            )
            if isinstance(ipv4_payload, dict):
                ip_addresses = ipv4_payload.get("ipAddress")
                if isinstance(ip_addresses, list) and ip_addresses:
                    first_ip = ip_addresses[0]
                    if isinstance(first_ip, dict):
                        metadata["private_ip"] = first_ip.get("privateIpAddress")
                        metadata["public_ip"] = first_ip.get("publicIpAddress")
        except json.JSONDecodeError:
            pass

    return {k: v for k, v in metadata.items() if v not in {None, ""}}


def _http_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
) -> str | None:
    request = Request(url=url, method=method)
    if headers:
        for key, value in headers.items():
            request.add_header(key, value)

    try:
        with urlopen(request, timeout=_METADATA_TIMEOUT_SECONDS) as response:
            content = response.read().decode("utf-8").strip()
            return content or None
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def _metadata_http_enabled(env: Mapping[str, str]) -> bool:
    flag = env.get("REFUA_DEPLOY_ENABLE_METADATA_HTTP", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _default_node_instance_type(*, provider: str, needs_gpu: bool) -> str:
    if needs_gpu:
        return _DEFAULT_NODE_TYPE_GPU.get(provider, "gpu-node")
    return _DEFAULT_NODE_TYPE_CPU.get(provider, "cpu-node")


def _sanitize_cluster_name(value: str) -> str:
    normalized = value.lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        return "refua"
    return normalized[:63].rstrip("-") or "refua"


def _merge_with_defaults(*, explicit: list[str], inferred: list[str]) -> list[str]:
    if explicit:
        return _dedupe([item.strip() for item in explicit if item.strip()])
    return _dedupe([item.strip() for item in inferred if item and item.strip()])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _first_non_empty(mapping: Mapping[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        numeric = int(part)
        if numeric < 0 or numeric > 255:
            return False
    return True
