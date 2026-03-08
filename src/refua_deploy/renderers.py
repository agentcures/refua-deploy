from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from refua_deploy.autodetect import ResolvedAutomation, resolve_automation
from refua_deploy.bootstrap import render_cluster_bootstrap
from refua_deploy.integration import (
    WorkspaceIntegration,
    ecosystem_packages,
    resolve_images,
)
from refua_deploy.models import DeploymentSpec
from refua_deploy.planner import build_plan

_GPU_PRESENT_LABEL_BY_VENDOR = {
    "nvidia": "nvidia.com/gpu.present",
    "amd": "amd.com/gpu.present",
    "intel": "intel.feature.node.kubernetes.io/gpu",
}


def render_bundle(
    spec: DeploymentSpec,
    workspace: WorkspaceIntegration,
    output_dir: str | Path,
) -> list[Path]:
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    resolved = resolve_automation(spec)

    plan_payload = build_plan(spec, workspace)
    _write_json(out_root / "plan.json", plan_payload)

    if spec.uses_kubernetes:
        manifest_paths = _render_kubernetes(spec, workspace, out_root, resolved)
        if spec.automation.bootstrap_cluster:
            manifest_paths.extend(render_cluster_bootstrap(spec, resolved, out_root))
    elif spec.uses_single_machine:
        manifest_paths = _render_single_machine(spec, workspace, out_root, resolved)
    else:
        manifest_paths = _render_private(spec, workspace, out_root, resolved)

    return [out_root / "plan.json", *manifest_paths]


def _render_kubernetes(
    spec: DeploymentSpec,
    workspace: WorkspaceIntegration,
    out_root: Path,
    resolved: ResolvedAutomation,
) -> list[Path]:
    include_mcp_service = spec.runtime.mcp.mode == "service"
    campaign_image, mcp_image = resolve_images(spec, workspace)
    mcp_allowed_hosts = ",".join(
        _allowed_hosts_with_port_variants(
            hosts=resolved.allowed_hosts,
            port=spec.runtime.mcp.port,
        )
    )
    k8s_dir = out_root / "kubernetes"
    k8s_dir.mkdir(parents=True, exist_ok=True)

    labels = _labels(spec)

    namespace_metadata: dict[str, Any] = {
        "name": spec.runtime.namespace,
        "labels": labels,
    }
    if spec.kubernetes.namespace_annotations:
        namespace_metadata["annotations"] = dict(spec.kubernetes.namespace_annotations)

    namespace_doc = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": namespace_metadata,
    }

    configmap_doc: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{spec.runtime.namespace}-config",
            "namespace": spec.runtime.namespace,
            "labels": labels,
        },
        "data": {
            "REFUA_CAMPAIGN_OPENCLAW_BASE_URL": spec.openclaw.base_url,
            "REFUA_CAMPAIGN_OPENCLAW_MODEL": spec.openclaw.model,
            "REFUA_CAMPAIGN_TIMEOUT_SECONDS": str(spec.openclaw.timeout_seconds),
            "REFUA_GPU_MODE": spec.gpu.mode,
            "REFUA_GPU_VENDOR": spec.gpu.vendor,
            "REFUA_GPU_COUNT": str(spec.gpu.count),
        },
    }
    if include_mcp_service:
        configmap_doc["data"].update(
            {
                "REFUA_MCP_TRANSPORT": spec.runtime.mcp.transport,
                "REFUA_MCP_HOST": "0.0.0.0",
                "REFUA_MCP_PORT": str(spec.runtime.mcp.port),
                "REFUA_MCP_ALLOWED_HOSTS": mcp_allowed_hosts,
                "REFUA_MCP_ALLOWED_ORIGINS": ",".join(resolved.allowed_origins),
            }
        )

    openclaw_secret_placeholder = "__set_openclaw_gateway_token__"
    mcp_secret_placeholder = "__set_mcp_auth_token__"
    if not spec.security.create_placeholder_secrets:
        openclaw_secret_placeholder = "managed-externally"
        mcp_secret_placeholder = "managed-externally"

    secrets_docs = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": spec.openclaw.token_secret_name,
                "namespace": spec.runtime.namespace,
                "labels": labels,
            },
            "type": "Opaque",
            "stringData": {
                spec.openclaw.token_secret_key: openclaw_secret_placeholder,
            },
        }
    ]
    if include_mcp_service:
        secrets_docs.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": spec.security.mcp_auth_secret_name,
                    "namespace": spec.runtime.namespace,
                    "labels": labels,
                },
                "type": "Opaque",
                "stringData": {
                    spec.security.mcp_auth_secret_key: mcp_secret_placeholder,
                },
            }
        )

    output_claim_name = f"{spec.runtime.namespace}-campaign-output"
    pvc_spec: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {
            "requests": {
                "storage": spec.storage.output_volume_size,
            }
        },
    }
    if spec.kubernetes.storage_class:
        pvc_spec["storageClassName"] = spec.kubernetes.storage_class

    pvc_doc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": output_claim_name,
            "namespace": spec.runtime.namespace,
            "labels": labels,
        },
        "spec": pvc_spec,
    }

    mcp_deployment_doc: dict[str, Any] | None = None
    mcp_service_doc: dict[str, Any] | None = None
    if include_mcp_service:
        mcp_container: dict[str, Any] = {
            "name": "refua-mcp",
            "image": mcp_image,
            "imagePullPolicy": "IfNotPresent",
            "command": ["python", "-m", "refua_mcp.server"],
            "ports": [
                {
                    "containerPort": spec.runtime.mcp.port,
                    "name": "http",
                }
            ],
            "env": [
                {
                    "name": "REFUA_MCP_TRANSPORT",
                    "value": spec.runtime.mcp.transport,
                },
                {
                    "name": "REFUA_MCP_HOST",
                    "value": "0.0.0.0",
                },
                {
                    "name": "REFUA_MCP_PORT",
                    "value": str(spec.runtime.mcp.port),
                },
                {
                    "name": "REFUA_MCP_ALLOWED_HOSTS",
                    "value": mcp_allowed_hosts,
                },
                {
                    "name": "REFUA_MCP_ALLOWED_ORIGINS",
                    "value": ",".join(resolved.allowed_origins),
                },
                {
                    "name": "REFUA_MCP_AUTH_TOKENS",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": spec.security.mcp_auth_secret_name,
                            "key": spec.security.mcp_auth_secret_key,
                        }
                    },
                },
                *_gpu_container_env(spec, enabled=spec.gpu.mcp_enabled),
            ],
        }
        gpu_resources = _gpu_resource_requests(spec, enabled=spec.gpu.mcp_enabled)
        if gpu_resources is not None:
            mcp_container["resources"] = gpu_resources

        mcp_pod_spec: dict[str, Any] = {
            "containers": [mcp_container],
        }
        _apply_gpu_pod_overrides(
            mcp_pod_spec,
            spec=spec,
            enabled=spec.gpu.mcp_enabled,
        )

        mcp_deployment_doc = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"{spec.runtime.namespace}-mcp",
                "namespace": spec.runtime.namespace,
                "labels": labels,
            },
            "spec": {
                "replicas": spec.runtime.mcp.replicas,
                "selector": {
                    "matchLabels": {
                        "app.kubernetes.io/component": "mcp",
                        "app.kubernetes.io/name": spec.name,
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            **labels,
                            "app.kubernetes.io/component": "mcp",
                        }
                    },
                    "spec": mcp_pod_spec,
                },
            },
        }

        mcp_service_doc = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": f"{spec.runtime.namespace}-mcp",
                "namespace": spec.runtime.namespace,
                "labels": labels,
            },
            "spec": {
                "type": spec.kubernetes.service_type,
                "selector": {
                    "app.kubernetes.io/component": "mcp",
                    "app.kubernetes.io/name": spec.name,
                },
                "ports": [
                    {
                        "name": "http",
                        "port": spec.runtime.mcp.port,
                        "targetPort": spec.runtime.mcp.port,
                    }
                ],
            },
        }

    campaign_container: dict[str, Any] = {
        "name": "ClawCures",
        "image": campaign_image,
        "imagePullPolicy": "IfNotPresent",
        "command": [
            "ClawCures",
            "run-autonomous",
            "--objective",
            spec.runtime.campaign.objective,
            "--max-rounds",
            str(spec.runtime.campaign.max_rounds),
            "--max-calls",
            str(spec.runtime.campaign.max_calls),
            "--output",
            spec.runtime.campaign.output_path,
        ],
        "env": [
            {
                "name": "REFUA_CAMPAIGN_OPENCLAW_BASE_URL",
                "value": spec.openclaw.base_url,
            },
            {
                "name": "REFUA_CAMPAIGN_OPENCLAW_MODEL",
                "value": spec.openclaw.model,
            },
            {
                "name": "REFUA_CAMPAIGN_TIMEOUT_SECONDS",
                "value": str(spec.openclaw.timeout_seconds),
            },
            {
                "name": "OPENCLAW_GATEWAY_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": spec.openclaw.token_secret_name,
                        "key": spec.openclaw.token_secret_key,
                    }
                },
            },
            *_gpu_container_env(spec, enabled=spec.gpu.campaign_enabled),
        ],
        "volumeMounts": [
            {
                "name": "campaign-output",
                "mountPath": "/var/lib/refua/output",
            }
        ],
    }
    campaign_gpu_resources = _gpu_resource_requests(
        spec, enabled=spec.gpu.campaign_enabled
    )
    if campaign_gpu_resources is not None:
        campaign_container["resources"] = campaign_gpu_resources

    campaign_pod_spec: dict[str, Any] = {
        "restartPolicy": "OnFailure",
        "containers": [campaign_container],
        "volumes": [
            {
                "name": "campaign-output",
                "persistentVolumeClaim": {
                    "claimName": output_claim_name,
                },
            }
        ],
    }
    _apply_gpu_pod_overrides(
        campaign_pod_spec,
        spec=spec,
        enabled=spec.gpu.campaign_enabled,
    )

    campaign_cronjob_doc = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {
            "name": f"{spec.runtime.namespace}-campaign",
            "namespace": spec.runtime.namespace,
            "labels": labels,
        },
        "spec": {
            "schedule": spec.runtime.campaign.schedule,
            "concurrencyPolicy": "Forbid",
            "successfulJobsHistoryLimit": 3,
            "failedJobsHistoryLimit": 3,
            "jobTemplate": {
                "spec": {
                    "template": {
                        "metadata": {
                            "labels": {
                                **labels,
                                "app.kubernetes.io/component": "campaign",
                            }
                        },
                        "spec": campaign_pod_spec,
                    }
                }
            },
        },
    }

    namespace_path = k8s_dir / "namespace.yaml"
    configmap_path = k8s_dir / "configmap.yaml"
    secrets_path = k8s_dir / "secrets.template.yaml"
    pvc_path = k8s_dir / "campaign-output-pvc.yaml"
    deployment_path = k8s_dir / "mcp-deployment.yaml"
    service_path = k8s_dir / "mcp-service.yaml"
    cronjob_path = k8s_dir / "campaign-cronjob.yaml"

    _write_yaml(namespace_path, namespace_doc)
    _write_yaml(configmap_path, configmap_doc)
    _write_yaml_documents(secrets_path, secrets_docs)
    _write_yaml(pvc_path, pvc_doc)
    _write_yaml(cronjob_path, campaign_cronjob_doc)
    if include_mcp_service:
        _write_yaml(deployment_path, mcp_deployment_doc or {})
        _write_yaml(service_path, mcp_service_doc or {})

    output_paths = [
        namespace_path,
        configmap_path,
        secrets_path,
        pvc_path,
        cronjob_path,
    ]
    if include_mcp_service:
        output_paths.extend([deployment_path, service_path])

    kustomization_resources = [
        "namespace.yaml",
        "configmap.yaml",
        "secrets.template.yaml",
        "campaign-output-pvc.yaml",
        "campaign-cronjob.yaml",
    ]
    if include_mcp_service:
        kustomization_resources.extend(["mcp-deployment.yaml", "mcp-service.yaml"])

    if spec.kubernetes.create_network_policy and include_mcp_service:
        network_policy_doc: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{spec.runtime.namespace}-mcp-ingress",
                "namespace": spec.runtime.namespace,
                "labels": labels,
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/component": "mcp",
                        "app.kubernetes.io/name": spec.name,
                    }
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "podSelector": {
                                    "matchLabels": {
                                        "app.kubernetes.io/component": "campaign",
                                    }
                                }
                            },
                            {
                                "podSelector": {
                                    "matchLabels": {
                                        "app.kubernetes.io/component": "mcp",
                                    }
                                }
                            },
                        ]
                    }
                ],
            },
        }
        if spec.network.expose_mcp:
            network_policy_doc["spec"]["ingress"].append(
                {
                    "from": [{"namespaceSelector": {}}],
                }
            )

        network_policy_path = k8s_dir / "network-policy.yaml"
        _write_yaml(network_policy_path, network_policy_doc)
        output_paths.append(network_policy_path)
        kustomization_resources.append("network-policy.yaml")

    if include_mcp_service and spec.network.expose_mcp and resolved.ingress_host:
        ingress_doc: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{spec.runtime.namespace}-mcp",
                "namespace": spec.runtime.namespace,
                "labels": labels,
            },
            "spec": {
                "rules": [
                    {
                        "host": resolved.ingress_host,
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": f"{spec.runtime.namespace}-mcp",
                                            "port": {"number": spec.runtime.mcp.port},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        }
        if spec.kubernetes.ingress_class:
            ingress_doc["spec"]["ingressClassName"] = spec.kubernetes.ingress_class

        ingress_path = k8s_dir / "mcp-ingress.yaml"
        _write_yaml(ingress_path, ingress_doc)
        output_paths.append(ingress_path)
        kustomization_resources.append("mcp-ingress.yaml")

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": kustomization_resources,
    }
    kustomization_path = k8s_dir / "kustomization.yaml"
    _write_yaml(kustomization_path, kustomization_doc)
    output_paths.append(kustomization_path)

    return output_paths


def _render_private(
    spec: DeploymentSpec,
    workspace: WorkspaceIntegration,
    out_root: Path,
    _resolved: ResolvedAutomation,
) -> list[Path]:
    campaign_image, _mcp_image = resolve_images(spec, workspace)
    private_dir = out_root / "private"
    private_dir.mkdir(parents=True, exist_ok=True)

    campaign_env: dict[str, Any] = {
        "REFUA_CAMPAIGN_OPENCLAW_BASE_URL": "${REFUA_CAMPAIGN_OPENCLAW_BASE_URL}",
        "REFUA_CAMPAIGN_OPENCLAW_MODEL": "${REFUA_CAMPAIGN_OPENCLAW_MODEL}",
        "REFUA_CAMPAIGN_TIMEOUT_SECONDS": "${REFUA_CAMPAIGN_TIMEOUT_SECONDS}",
        "OPENCLAW_GATEWAY_TOKEN": "${OPENCLAW_GATEWAY_TOKEN}",
    }
    campaign_env.update(_gpu_compose_env(spec, enabled=spec.gpu.campaign_enabled))

    campaign_service: dict[str, Any] = {
        "image": campaign_image,
        "command": [
            "ClawCures",
            "run-autonomous",
            "--objective",
            spec.runtime.campaign.objective,
            "--max-rounds",
            str(spec.runtime.campaign.max_rounds),
            "--max-calls",
            str(spec.runtime.campaign.max_calls),
            "--output",
            spec.runtime.campaign.output_path,
        ],
        "environment": campaign_env,
        "volumes": ["./outputs:/var/lib/refua/output"],
        "restart": "no",
    }
    if spec.gpu.mode == "required" and spec.gpu.campaign_enabled:
        campaign_service["gpus"] = "all"

    compose_doc: dict[str, Any] = {
        "services": {
            "campaign_runner": campaign_service,
        }
    }

    compose_path = private_dir / "docker-compose.yaml"
    _write_yaml(compose_path, compose_doc)

    env_template = "\n".join(
        [
            f"REFUA_CAMPAIGN_OPENCLAW_BASE_URL={spec.openclaw.base_url}",
            f"REFUA_CAMPAIGN_OPENCLAW_MODEL={spec.openclaw.model}",
            f"REFUA_CAMPAIGN_TIMEOUT_SECONDS={spec.openclaw.timeout_seconds}",
            "OPENCLAW_GATEWAY_TOKEN=replace-me",
            "",
        ]
    )
    env_path = private_dir / ".env.template"
    env_path.write_text(env_template, encoding="utf-8")

    return [compose_path, env_path]


def _render_single_machine(
    spec: DeploymentSpec,
    workspace: WorkspaceIntegration,
    out_root: Path,
    resolved: ResolvedAutomation,
) -> list[Path]:
    single_dir = out_root / "single-machine"
    single_dir.mkdir(parents=True, exist_ok=True)

    install_path = single_dir / "install-ecosystem.sh"
    env_path = single_dir / ".env.template"
    mcp_path = single_dir / "run-mcp.sh"
    campaign_path = single_dir / "run-campaign.sh"
    studio_path = single_dir / "run-studio.sh"

    packages = ecosystem_packages()
    workspace_root_default = str(workspace.root)
    install_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        f'WORKSPACE_ROOT="${{REFUA_ECOSYSTEM_WORKSPACE_ROOT:-{workspace_root_default}}}"',
        'VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-refua}"',
        'if [[ -z "${PYTHON_BIN:-}" ]]; then',
        '  for _CANDIDATE in python3.12 python3.13 python3.11 python3; do',
        '    if command -v "$_CANDIDATE" >/dev/null 2>&1; then',
        '      PYTHON_BIN="$_CANDIDATE"',
        "      break",
        "    fi",
        "  done",
        "fi",
        'if [[ -z "${PYTHON_BIN:-}" ]]; then',
        '  echo "No python3 interpreter found. Install Python 3.12." >&2',
        "  exit 1",
        "fi",
        (
            'PYTHON_VERSION="$("$PYTHON_BIN" -c '
            '\'import sys; print("{}.{}".format('
            'sys.version_info.major, sys.version_info.minor))\')"'
        ),
        'case "$PYTHON_VERSION" in',
        "  3.12) ;;",
        (
            '  *) echo "Unsupported Python version $PYTHON_VERSION. '
            'Full ecosystem install currently requires Python 3.12." >&2; exit 1 ;;'
        ),
        "esac",
        "",
        '"$PYTHON_BIN" -m venv "$VENV_DIR"',
        '"$VENV_DIR/bin/python" -m pip install --upgrade pip',
        "",
        "PACKAGES=(",
        *[f'  "{package_name}"' for package_name in packages],
        ")",
        "",
        'for PACKAGE_NAME in "${PACKAGES[@]}"; do',
        '  LOCAL_PACKAGE_PATH="$WORKSPACE_ROOT/$PACKAGE_NAME"',
        '  if [[ -f "$LOCAL_PACKAGE_PATH/pyproject.toml" ]]; then',
        '    "$VENV_DIR/bin/python" -m pip install --upgrade -e "$LOCAL_PACKAGE_PATH"',
        "  else",
        '    "$VENV_DIR/bin/python" -m pip install --upgrade "$PACKAGE_NAME"',
        "  fi",
        "done",
        "",
        'echo "Refua ecosystem installed in $VENV_DIR"',
        'echo "Workspace root used for local editable installs: $WORKSPACE_ROOT"',
        'echo "Create .env from .env.template, then run ./run-studio.sh or ./run-campaign.sh"',
    ]
    _write_script(install_path, install_lines)

    mcp_allowed_hosts = ",".join(
        _allowed_hosts_with_port_variants(
            hosts=resolved.allowed_hosts,
            port=spec.runtime.mcp.port,
        )
    )
    mcp_allowed_origins = ",".join(resolved.allowed_origins)
    campaign_objective_literal = json.dumps(spec.runtime.campaign.objective)
    env_template = "\n".join(
        [
            f"REFUA_CAMPAIGN_OPENCLAW_BASE_URL={spec.openclaw.base_url}",
            f"REFUA_CAMPAIGN_OPENCLAW_MODEL={spec.openclaw.model}",
            f"REFUA_CAMPAIGN_TIMEOUT_SECONDS={spec.openclaw.timeout_seconds}",
            "OPENCLAW_GATEWAY_TOKEN=replace-me",
            "",
            f"REFUA_CAMPAIGN_OBJECTIVE={campaign_objective_literal}",
            f"REFUA_CAMPAIGN_MAX_ROUNDS={spec.runtime.campaign.max_rounds}",
            f"REFUA_CAMPAIGN_MAX_CALLS={spec.runtime.campaign.max_calls}",
            "REFUA_CAMPAIGN_OUTPUT_PATH=./outputs/latest_run.json",
            "",
            f"REFUA_MCP_TRANSPORT={spec.runtime.mcp.transport}",
            "REFUA_MCP_HOST=127.0.0.1",
            f"REFUA_MCP_PORT={spec.runtime.mcp.port}",
            f"REFUA_MCP_ALLOWED_HOSTS={mcp_allowed_hosts}",
            f"REFUA_MCP_ALLOWED_ORIGINS={mcp_allowed_origins}",
            "REFUA_MCP_AUTH_TOKENS=replace-me",
            "",
            f"REFUA_GPU_MODE={spec.gpu.mode}",
            f"REFUA_GPU_VENDOR={spec.gpu.vendor}",
            f"REFUA_GPU_COUNT={spec.gpu.count}",
            "",
            "CLAWCURES_UI_HOST=127.0.0.1",
            "CLAWCURES_UI_PORT=8787",
            "CLAWCURES_UI_DATA_DIR=.clawcures-ui",
            "CLAWCURES_UI_WORKSPACE_ROOT=.",
            "CLAWCURES_UI_AUTH_TOKENS=replace-me-viewer-token",
            "CLAWCURES_UI_OPERATOR_TOKENS=replace-me-operator-token",
            "CLAWCURES_UI_ADMIN_TOKENS=replace-me-admin-token",
            "",
        ]
    )
    env_path.write_text(env_template, encoding="utf-8")

    mcp_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'if [[ -f "$ROOT_DIR/.env" ]]; then',
        "  set -a",
        '  source "$ROOT_DIR/.env"',
        "  set +a",
        "fi",
        "",
        'PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv-refua/bin/python}"',
        'if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi',
        "",
        'export REFUA_MCP_TRANSPORT="${REFUA_MCP_TRANSPORT:-streamable-http}"',
        'export REFUA_MCP_HOST="${REFUA_MCP_HOST:-127.0.0.1}"',
        'export REFUA_MCP_PORT="${REFUA_MCP_PORT:-8000}"',
        (
            'export REFUA_MCP_ALLOWED_HOSTS="${REFUA_MCP_ALLOWED_HOSTS:-'
            'localhost,127.0.0.1}"'
        ),
        (
            'export REFUA_MCP_ALLOWED_ORIGINS="${REFUA_MCP_ALLOWED_ORIGINS:-'
            'http://localhost:${REFUA_MCP_PORT},http://127.0.0.1:${REFUA_MCP_PORT}}"'
        ),
        "",
        '"$PYTHON_BIN" -m refua_mcp.server',
    ]
    _write_script(mcp_path, mcp_lines)

    campaign_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'if [[ -f "$ROOT_DIR/.env" ]]; then',
        "  set -a",
        '  source "$ROOT_DIR/.env"',
        "  set +a",
        "fi",
        "",
        'PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv-refua/bin/python}"',
        'if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi',
        'mkdir -p "$ROOT_DIR/outputs"',
        'CAMPAIGN_OBJECTIVE="${REFUA_CAMPAIGN_OBJECTIVE:-Design and execute a Refua campaign}"',
        'OUTPUT_PATH="${REFUA_CAMPAIGN_OUTPUT_PATH:-$ROOT_DIR/outputs/latest_run.json}"',
        "",
        '"$PYTHON_BIN" -m refua_campaign.cli run-autonomous \\',
        '  --objective "$CAMPAIGN_OBJECTIVE" \\',
        f'  --max-rounds "${{REFUA_CAMPAIGN_MAX_ROUNDS:-{spec.runtime.campaign.max_rounds}}}" \\',
        f'  --max-calls "${{REFUA_CAMPAIGN_MAX_CALLS:-{spec.runtime.campaign.max_calls}}}" \\',
        '  --output "$OUTPUT_PATH"',
    ]
    _write_script(campaign_path, campaign_lines)

    studio_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'if [[ -f "$ROOT_DIR/.env" ]]; then',
        "  set -a",
        '  source "$ROOT_DIR/.env"',
        "  set +a",
        "fi",
        "",
        'PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv-refua/bin/python}"',
        'if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi',
        'STUDIO_HOST="${CLAWCURES_UI_HOST:-${REFUA_STUDIO_HOST:-127.0.0.1}}"',
        'STUDIO_PORT="${CLAWCURES_UI_PORT:-${REFUA_STUDIO_PORT:-8787}}"',
        'DEFAULT_STUDIO_DATA_DIR="$ROOT_DIR/.clawcures-ui"',
        'if [[ -z "${CLAWCURES_UI_DATA_DIR:-}" && -z "${REFUA_STUDIO_DATA_DIR:-}" && ! -d "$DEFAULT_STUDIO_DATA_DIR" && -d "$ROOT_DIR/.refua-studio" ]]; then',
        '  DEFAULT_STUDIO_DATA_DIR="$ROOT_DIR/.refua-studio"',
        "fi",
        'STUDIO_DATA_DIR="${CLAWCURES_UI_DATA_DIR:-${REFUA_STUDIO_DATA_DIR:-$DEFAULT_STUDIO_DATA_DIR}}"',
        'STUDIO_WORKSPACE_ROOT="${CLAWCURES_UI_WORKSPACE_ROOT:-${REFUA_STUDIO_WORKSPACE_ROOT:-$ROOT_DIR}}"',
        'STUDIO_AUTH_TOKENS="${CLAWCURES_UI_AUTH_TOKENS:-${REFUA_STUDIO_AUTH_TOKENS:-}}"',
        'STUDIO_OPERATOR_TOKENS="${CLAWCURES_UI_OPERATOR_TOKENS:-${REFUA_STUDIO_OPERATOR_TOKENS:-}}"',
        'STUDIO_ADMIN_TOKENS="${CLAWCURES_UI_ADMIN_TOKENS:-${REFUA_STUDIO_ADMIN_TOKENS:-}}"',
        "",
        "STUDIO_ARGS=(",
        '  --host "$STUDIO_HOST"',
        '  --port "$STUDIO_PORT"',
        '  --data-dir "$STUDIO_DATA_DIR"',
        '  --workspace-root "$STUDIO_WORKSPACE_ROOT"',
        ")",
        "",
        'if [[ -n "$STUDIO_AUTH_TOKENS" ]]; then',
        '  IFS="," read -r -a _VIEWER_TOKENS <<< "$STUDIO_AUTH_TOKENS"',
        '  for _TOKEN in "${_VIEWER_TOKENS[@]}"; do',
        '    _TOKEN="${_TOKEN#"${_TOKEN%%[![:space:]]*}"}"',
        '    _TOKEN="${_TOKEN%"${_TOKEN##*[![:space:]]}"}"',
        '    [[ -n "$_TOKEN" ]] && STUDIO_ARGS+=(--auth-token "$_TOKEN")',
        "  done",
        "fi",
        "",
        'if [[ -n "$STUDIO_OPERATOR_TOKENS" ]]; then',
        '  IFS="," read -r -a _OPERATOR_TOKENS <<< "$STUDIO_OPERATOR_TOKENS"',
        '  for _TOKEN in "${_OPERATOR_TOKENS[@]}"; do',
        '    _TOKEN="${_TOKEN#"${_TOKEN%%[![:space:]]*}"}"',
        '    _TOKEN="${_TOKEN%"${_TOKEN##*[![:space:]]}"}"',
        '    [[ -n "$_TOKEN" ]] && STUDIO_ARGS+=(--operator-token "$_TOKEN")',
        "  done",
        "fi",
        "",
        'if [[ -n "$STUDIO_ADMIN_TOKENS" ]]; then',
        '  IFS="," read -r -a _ADMIN_TOKENS <<< "$STUDIO_ADMIN_TOKENS"',
        '  for _TOKEN in "${_ADMIN_TOKENS[@]}"; do',
        '    _TOKEN="${_TOKEN#"${_TOKEN%%[![:space:]]*}"}"',
        '    _TOKEN="${_TOKEN%"${_TOKEN##*[![:space:]]}"}"',
        '    [[ -n "$_TOKEN" ]] && STUDIO_ARGS+=(--admin-token "$_TOKEN")',
        "  done",
        "fi",
        "",
        '"$PYTHON_BIN" -m clawcures_ui "${STUDIO_ARGS[@]}"',
    ]
    _write_script(studio_path, studio_lines)

    return [install_path, env_path, mcp_path, campaign_path, studio_path]


def _gpu_container_env(spec: DeploymentSpec, *, enabled: bool) -> list[dict[str, str]]:
    if not enabled or spec.gpu.mode == "off":
        return []

    env = [
        {"name": "REFUA_GPU_MODE", "value": spec.gpu.mode},
        {"name": "REFUA_GPU_VENDOR", "value": spec.gpu.vendor},
        {"name": "REFUA_GPU_COUNT", "value": str(spec.gpu.count)},
    ]
    if spec.gpu.vendor == "nvidia":
        env.extend(
            [
                {"name": "CUDA_VISIBLE_DEVICES", "value": "all"},
                {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"},
                {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,utility"},
            ]
        )
    return env


def _allowed_hosts_with_port_variants(*, hosts: list[str], port: int) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        normalized = host.strip()
        if not normalized:
            continue

        candidates = [normalized]
        if ":" not in normalized:
            candidates.append(f"{normalized}:{port}")

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def _gpu_compose_env(spec: DeploymentSpec, *, enabled: bool) -> dict[str, str]:
    if not enabled or spec.gpu.mode == "off":
        return {}

    env = {
        "REFUA_GPU_MODE": spec.gpu.mode,
        "REFUA_GPU_VENDOR": spec.gpu.vendor,
        "REFUA_GPU_COUNT": str(spec.gpu.count),
    }
    if spec.gpu.vendor == "nvidia":
        env["CUDA_VISIBLE_DEVICES"] = "all"
        env["NVIDIA_VISIBLE_DEVICES"] = "all"
        env["NVIDIA_DRIVER_CAPABILITIES"] = "compute,utility"
    return env


def _gpu_resource_requests(
    spec: DeploymentSpec, *, enabled: bool
) -> dict[str, Any] | None:
    if not enabled or spec.gpu.mode != "required":
        return None

    return {
        "requests": {
            spec.gpu.resource_name: spec.gpu.count,
        },
        "limits": {
            spec.gpu.resource_name: spec.gpu.count,
        },
    }


def _apply_gpu_pod_overrides(
    pod_spec: dict[str, Any],
    *,
    spec: DeploymentSpec,
    enabled: bool,
) -> None:
    if not enabled or spec.gpu.mode == "off":
        return

    if spec.gpu.node_selector:
        pod_spec["nodeSelector"] = dict(spec.gpu.node_selector)
    elif spec.gpu.mode == "required":
        label_key = _GPU_PRESENT_LABEL_BY_VENDOR.get(spec.gpu.vendor)
        if label_key:
            pod_spec["nodeSelector"] = {label_key: "true"}

    if spec.gpu.mode == "auto":
        label_key = _GPU_PRESENT_LABEL_BY_VENDOR.get(spec.gpu.vendor)
        if label_key:
            pod_spec["affinity"] = {
                "nodeAffinity": {
                    "preferredDuringSchedulingIgnoredDuringExecution": [
                        {
                            "weight": 100,
                            "preference": {
                                "matchExpressions": [
                                    {
                                        "key": label_key,
                                        "operator": "In",
                                        "values": ["true"],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    if spec.gpu.toleration_key:
        pod_spec["tolerations"] = [
            {
                "key": spec.gpu.toleration_key,
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ]


def _labels(spec: DeploymentSpec) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": spec.name,
        "app.kubernetes.io/part-of": "refua",
        "app.kubernetes.io/managed-by": "refua-deploy",
        "refua.cloud.provider": spec.cloud.provider,
        "refua.cloud.visibility": spec.cloud.visibility,
        "refua.kubernetes.distribution": spec.kubernetes.distribution,
        "refua.gpu.mode": spec.gpu.mode,
        "refua.gpu.vendor": spec.gpu.vendor,
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_yaml_documents(path: Path, payloads: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "---\n".join(
        yaml.safe_dump(payload, sort_keys=False) for payload in payloads
    )
    path.write_text(rendered, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_script(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)
