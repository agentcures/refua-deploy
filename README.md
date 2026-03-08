# refua-deploy

`refua-deploy` generates deployment bundles for running Refua campaigns across public and private clouds.

It integrates with the Refua ecosystem packages:

- `refua`
- `refua-data`
- `refua-clinical`
- `refua-preclinical`
- `refua-regulatory`
- `refua-bench`
- `refua-wetlab`
- `refua-notebook`
- `refua-mcp`
- `ClawCures`
- `clawcures-ui`
- `refua-deploy`

When these projects are present, `refua-deploy` auto-detects their versions and can install the full Refua ecosystem (including `clawcures-ui`).

## Guidebook

New to deploying the agent and Studio?
See the step-by-step guidebook: [GUIDEBOOK.md](GUIDEBOOK.md).

## Super Simple

If you just want it working with sensible defaults:

```bash
cd refua-deploy
poetry install
poetry run refua-deploy install-ecosystem
poetry run refua-deploy init --output deploy.yaml --name refua-prod --visibility public --provider aws
poetry run refua-deploy render --config deploy.yaml --output-dir dist
bash dist/bootstrap/cluster-bootstrap.sh
```

What this does automatically:

- Picks Kubernetes as orchestrator for public cloud.
- Enables network auto-discovery and fills ingress/host/origin defaults.
- Enables cluster bootstrap artifact generation.
- Enables GPU `auto` mode by default.
- Installs the full Refua ecosystem from PyPI (including Studio).
- Detects local `ClawCures` and `refua-mcp` versions for image tags.

## Goals

- Minimal required inputs.
- Automatic network defaults.
- Automatic cluster bootstrap artifacts.
- GPU support that is transparent by default.

## Features

- Validated deployment config for:
  - Public cloud providers: `aws`, `gcp`, `azure`, `oci`, `digitalocean`, `linode`, `vultr`, `hetzner`, `ibm`, `alibaba`, `scaleway`, `exoscale`
  - Private cloud providers: `onprem`, `openstack`, `vmware`, `baremetal`, `proxmox`, `nutanix`
- Runtime target selection:
  - `kubernetes` renderer
  - `compose` renderer
  - `single-machine` lightweight renderer
- Automatic network inference:
  - Ingress host from explicit config, env, or inferred metadata defaults
  - Allowed hosts/origins inferred when omitted
- Automatic bootstrap artifacts (Kubernetes targets):
  - `bootstrap/cluster-bootstrap.sh`
  - `bootstrap/metadata.auto.json`
  - `bootstrap/network.auto.env`
- Kubernetes bundle renderer:
  - Namespace
  - ConfigMap
  - Secret templates
  - Campaign output PVC
  - `ClawCures` CronJob
  - Optional `refua-mcp` Deployment + Service (`runtime.mcp.mode=service`)
  - Optional Ingress
  - Optional NetworkPolicy
  - `kustomization.yaml`
- Compose bundle renderer:
  - `campaign_runner` service (runs `ClawCures` with in-process MCP execution)
  - `.env.template`
- Single-machine lightweight renderer:
  - `single-machine/install-ecosystem.sh`
  - `single-machine/.env.template`
  - `single-machine/run-mcp.sh`
  - `single-machine/run-campaign.sh`
  - `single-machine/run-studio.sh`
- Full ecosystem installer:
  - `install-ecosystem` command installs the Refua ecosystem from PyPI in dependency-safe order
- GPU-aware deployment controls:
  - `gpu.mode=auto` (default): GPU-friendly scheduling/runtime hints with CPU fallback.
  - `gpu.mode=required`: hard GPU requests/limits for Kubernetes and `gpus: all` for Compose.
  - `gpu.mode=off`: disables GPU behavior.
- Plan output (`plan.json`) for CI/CD review and approvals.
- Runtime lifecycle commands:
  - `apply` (render + apply manifests / compose up)
  - `status` (kubectl or compose status, plus single-machine artifact status)
  - `destroy` (kubectl delete / compose down)
  - `doctor` (preflight diagnostics for toolchain + rendered artifacts)

## Install

```bash
cd refua-deploy
poetry install
```

Install the full Refua ecosystem (including Studio):

```bash
poetry run refua-deploy install-ecosystem
```

## Quick Start

Generate a starter config with maximum automation:

```bash
poetry run refua-deploy init \
  --output deploy/public.yaml \
  --name refua-oncology-prod \
  --visibility public \
  --provider aws \
  --orchestrator kubernetes \
  --provisioning-level auto \
  --gpu-mode auto \
  --gpu-vendor nvidia
```

Validate and preview plan:

```bash
poetry run refua-deploy plan \
  --config deploy/public.yaml \
  --output deploy/plan.json
```

Render artifacts:

```bash
poetry run refua-deploy render \
  --config deploy/public.yaml \
  --output-dir dist/public
```

Apply rendered runtime:

```bash
poetry run refua-deploy apply \
  --config deploy/public.yaml \
  --output-dir dist/public
```

Check runtime status:

```bash
poetry run refua-deploy status \
  --config deploy/public.yaml \
  --output-dir dist/public
```

Run deployment diagnostics:

```bash
poetry run refua-deploy doctor \
  --config deploy/public.yaml \
  --output-dir dist/public
```

Run generated bootstrap script:

```bash
bash dist/public/bootstrap/cluster-bootstrap.sh
```

Private cloud with compose:

```bash
poetry run refua-deploy init \
  --output deploy/private.yaml \
  --visibility private \
  --provider onprem \
  --orchestrator compose
```

Private cloud with Kubernetes (for example k3s/rke2):

```bash
poetry run refua-deploy init \
  --output deploy/private-k8s.yaml \
  --visibility private \
  --provider vmware \
  --orchestrator kubernetes
```

Single-machine lightweight bundle:

```bash
poetry run refua-deploy init \
  --output deploy/single-machine.yaml \
  --visibility private \
  --provider onprem \
  --orchestrator single-machine
poetry run refua-deploy render \
  --config deploy/single-machine.yaml \
  --output-dir dist/single-machine
```

## Metadata Auto-Discovery

`refua-deploy` can infer network/cluster context from:

- Explicit config values (highest priority)
- Environment variables
- Cloud metadata endpoints (when enabled)

Control flag:

- `REFUA_DEPLOY_ENABLE_METADATA_HTTP=0` disables HTTP metadata probing.

Useful environment overrides:

- `REFUA_INGRESS_HOST`
- `REFUA_PUBLIC_IP`
- `REFUA_PRIVATE_IP`
- `REFUA_AWS_VPC_ID`
- `REFUA_AWS_SUBNET_IDS`
- `REFUA_GCP_NETWORK`
- `REFUA_GCP_SUBNETWORK`
- `REFUA_AZURE_RESOURCE_GROUP`

## Config Schema

Top-level keys:

- `name`
- `cloud.visibility`
- `cloud.provider`
- `openclaw.base_url` (required)
- `runtime`:
  - `namespace`
  - `orchestrator` (`kubernetes`, `compose`, or `single-machine`)
  - `campaign`
  - `mcp`
    - `mode` (`inprocess` default, or `service`)
- `kubernetes`:
  - `distribution` (`eks`, `gke`, `aks`, `oke`, `doks`, `lke`, `vke`, `hke`, `iks`, `ack`, `ske`, `k3s`, `rke2`, `openshift`, `talos`, `kubeadm`, `generic`)
  - `service_type` (`ClusterIP`, `NodePort`, `LoadBalancer`)
  - `ingress_class`
  - `storage_class`
  - `create_network_policy`
  - `namespace_annotations`
- `gpu`:
  - `mode` (`off`, `auto`, `required`)
  - `vendor` (`nvidia`, `amd`, `intel`)
  - `count`
  - `resource_name`
  - `mcp_enabled`
  - `campaign_enabled`
  - `node_selector`
  - `toleration_key`
- `automation`:
  - `auto_discover_network`
  - `bootstrap_cluster`
  - `provisioning_level` (`manual`, `assisted`, `auto`)
  - `cluster_name`
  - `kubernetes_version`
  - `node_count`
  - `node_instance_type`
  - `node_disk_gb`
- `network`
- `security`
- `storage`

Examples:

- `examples/public_aws.yaml`
- `examples/private_onprem.yaml`

## Integration Details

Generated artifacts follow existing Refua runtime contracts:

- Campaign env vars:
  - `REFUA_CAMPAIGN_OPENCLAW_BASE_URL`
  - `REFUA_CAMPAIGN_OPENCLAW_MODEL`
  - `REFUA_CAMPAIGN_TIMEOUT_SECONDS`
  - `OPENCLAW_GATEWAY_TOKEN`
- MCP runtime env vars (Kubernetes `refua-mcp` deployment):
  - `REFUA_MCP_TRANSPORT`
  - `REFUA_MCP_HOST`
  - `REFUA_MCP_PORT`
  - `REFUA_MCP_ALLOWED_HOSTS`
  - `REFUA_MCP_ALLOWED_ORIGINS`
  - `REFUA_MCP_AUTH_TOKENS`
- Studio auth env vars (single-machine `.env.template` + `run-studio.sh`):
  - `CLAWCURES_UI_AUTH_TOKENS`
  - `CLAWCURES_UI_OPERATOR_TOKENS`
  - `CLAWCURES_UI_ADMIN_TOKENS`
  Legacy `REFUA_STUDIO_*` env vars are also accepted by generated scripts.
- GPU runtime env vars:
  - `REFUA_GPU_MODE`
  - `REFUA_GPU_VENDOR`
  - `REFUA_GPU_COUNT`
  - vendor hints like `CUDA_VISIBLE_DEVICES`, `NVIDIA_VISIBLE_DEVICES` where relevant

## Development

Run checks:

```bash
poetry run ruff check src tests
poetry run mypy src
poetry run pytest
```

Build package:

```bash
poetry build
```
