# Refua Deploy Guidebook

This guidebook is for new users who want to deploy:

- the Refua agent (`ClawCures`)
- ClawCures UI (`clawcures-ui`)

The fastest path is a single machine deployment, then moving to Kubernetes when needed.

## 1. What You Are Deploying

- `ClawCures`:
  the campaign agent that plans and executes discovery workflows.
- `refua-mcp`:
  the tool/runtime server used by the agent.
- `clawcures-ui`:
  the web control plane for operating campaigns and reviewing outputs.

## 2. Prerequisites

- Python `3.11`-`3.13`
- `pip`
- `poetry`
- OpenClaw endpoint and token

Optional:

- NVIDIA GPU with compatible drivers for faster workloads

## 3. Install `refua-deploy`

```bash
cd refua-deploy
poetry install
```

You can also install ecosystem packages directly from PyPI:

```bash
poetry run refua-deploy install-ecosystem
```

## 4. Recommended: Single-Machine Deployment

### 4.1 Generate config

```bash
poetry run refua-deploy init \
  --output deploy/single-machine.yaml \
  --name refua-local \
  --visibility private \
  --provider onprem \
  --orchestrator single-machine
```

### 4.2 Render artifacts

```bash
poetry run refua-deploy render \
  --config deploy/single-machine.yaml \
  --output-dir dist
```

This creates:

- `dist/single-machine/install-ecosystem.sh`
- `dist/single-machine/.env.template`
- `dist/single-machine/run-mcp.sh`
- `dist/single-machine/run-campaign.sh`
- `dist/single-machine/run-studio.sh`

### 4.3 Install the ecosystem

```bash
bash dist/single-machine/install-ecosystem.sh
```

### 4.4 Configure environment

```bash
cp dist/single-machine/.env.template dist/single-machine/.env
```

Edit `dist/single-machine/.env` and set at least:

- `REFUA_CAMPAIGN_OPENCLAW_BASE_URL`
- `OPENCLAW_GATEWAY_TOKEN`

### 4.5 Start services

Terminal 1:

```bash
bash dist/single-machine/run-mcp.sh
```

Terminal 2:

```bash
bash dist/single-machine/run-studio.sh
```

Optional Terminal 3 (launch the agent directly):

```bash
bash dist/single-machine/run-campaign.sh
```

### 4.6 Verify

- Studio UI: `http://127.0.0.1:8787`
- MCP service port from `.env` (default `8000`)
- Campaign outputs: `dist/single-machine/outputs/`

## 5. Kubernetes Deployment (Team/Prod Path)

Use this when you need multi-node scheduling, stronger isolation, and cluster ingress.

### 5.1 Generate config

```bash
poetry run refua-deploy init \
  --output deploy/public.yaml \
  --name refua-prod \
  --visibility public \
  --provider aws \
  --orchestrator kubernetes
```

### 5.2 Render and review

```bash
poetry run refua-deploy plan --config deploy/public.yaml --output deploy/plan.json
poetry run refua-deploy render --config deploy/public.yaml --output-dir dist/public
```

### 5.3 Bootstrap/apply

```bash
bash dist/public/bootstrap/cluster-bootstrap.sh
```

Then apply manifests with your standard Kubernetes workflow (`kubectl`, Argo CD, Flux, CI/CD).

## 6. Troubleshooting

- `OPENCLAW_GATEWAY_TOKEN` missing:
  set token in `.env` or secret templates before running agent/studio.
- `Address already in use`:
  change `REFUA_MCP_PORT` and/or `CLAWCURES_UI_PORT`.
- Import/runtime dependency errors:
  re-run `install-ecosystem.sh` or `refua-deploy install-ecosystem`.
- Studio runs but tools unavailable:
  ensure `run-mcp.sh` is active and `REFUA_MCP_*` values match.

## 7. Next Steps

- Harden secrets and tokens for your environment.
- Enable `runtime.mcp.mode=service` for remote MCP access patterns.
- Move from single-machine to Kubernetes once operational patterns stabilize.
