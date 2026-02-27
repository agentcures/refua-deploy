from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from refua_deploy.config import (
    PRIVATE_PROVIDERS,
    PUBLIC_PROVIDERS,
    SUPPORTED_GPU_MODES,
    SUPPORTED_GPU_VENDORS,
    SUPPORTED_ORCHESTRATORS,
    SUPPORTED_PROVISIONING_LEVELS,
    dump_mapping_yaml,
    load_spec,
    starter_mapping,
)
from refua_deploy.integration import (
    WorkspaceIntegration,
    discover_workspace,
    ecosystem_packages,
    resolve_images,
)
from refua_deploy.planner import build_plan
from refua_deploy.renderers import render_bundle

_ECOSYSTEM_PACKAGES = ecosystem_packages()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refua-deploy",
        description=(
            "Provision deployment artifacts for Refua campaign workloads on public and "
            "private cloud environments."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Generate a starter deployment config")
    init_parser.add_argument("--output", type=Path, required=True, help="Output YAML config path")
    init_parser.add_argument("--name", default="ClawCures-prod", help="Deployment name")
    init_parser.add_argument(
        "--visibility",
        choices=["public", "private"],
        default="public",
        help="Cloud visibility",
    )
    init_parser.add_argument(
        "--provider",
        help=(
            f"Cloud provider. Public: {', '.join(sorted(PUBLIC_PROVIDERS))}. "
            f"Private: {', '.join(sorted(PRIVATE_PROVIDERS))}"
        ),
    )
    init_parser.add_argument(
        "--orchestrator",
        choices=sorted(SUPPORTED_ORCHESTRATORS),
        help=(
            "Runtime target for rendered artifacts. Defaults to 'kubernetes' for public "
            "cloud and 'compose' for private cloud."
        ),
    )
    init_parser.add_argument(
        "--gpu-mode",
        choices=sorted(SUPPORTED_GPU_MODES),
        default="auto",
        help=(
            "GPU strategy: 'auto' prefers GPU while allowing CPU fallback, "
            "'required' enforces GPU scheduling, 'off' disables GPU."
        ),
    )
    init_parser.add_argument(
        "--gpu-vendor",
        choices=sorted(SUPPORTED_GPU_VENDORS),
        default="nvidia",
        help="GPU vendor family for resource and scheduling hints.",
    )
    init_parser.add_argument(
        "--provisioning-level",
        choices=sorted(SUPPORTED_PROVISIONING_LEVELS),
        default="auto",
        help=(
            "Automation level: 'auto' maximizes inference/bootstrap, "
            "'assisted' keeps bootstrap artifacts without hard assumptions, "
            "'manual' disables metadata-driven automation."
        ),
    )
    init_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output config when it already exists",
    )
    init_parser.set_defaults(handler=_cmd_init)

    plan_parser = subparsers.add_parser("plan", help="Validate a config and emit deployment plan")
    plan_parser.add_argument("--config", type=Path, required=True, help="Deployment config file")
    plan_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    plan_parser.add_argument("--output", type=Path, help="Optional JSON plan file")
    plan_parser.set_defaults(handler=_cmd_plan)

    render_parser = subparsers.add_parser(
        "render",
        help="Render deployment manifests and plan artifacts",
    )
    render_parser.add_argument("--config", type=Path, required=True, help="Deployment config file")
    render_parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for generated artifacts",
    )
    render_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    render_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow rendering into a non-empty output directory",
    )
    render_parser.set_defaults(handler=_cmd_render)

    install_parser = subparsers.add_parser(
        "install-ecosystem",
        help="Install the Refua ecosystem from PyPI",
    )
    install_parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to invoke pip",
    )
    install_parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Pass --upgrade to pip install",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pip commands without executing them",
    )
    install_parser.set_defaults(handler=_cmd_install_ecosystem)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return int(args.handler(args))
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _cmd_init(args: argparse.Namespace) -> int:
    if args.output.exists() and not args.force:
        raise ValueError("Output config already exists. Use --force to overwrite.")

    visibility = str(args.visibility)
    provider = _resolve_provider(visibility=visibility, provider=args.provider)

    workspace = discover_workspace(args.workspace_root)
    campaign_image, mcp_image = _resolve_default_images(workspace)

    payload = starter_mapping(
        name=str(args.name),
        visibility=visibility,
        provider=provider,
        campaign_image=campaign_image,
        mcp_image=mcp_image,
        orchestrator=args.orchestrator,
        gpu_mode=args.gpu_mode,
        gpu_vendor=args.gpu_vendor,
        provisioning_level=args.provisioning_level,
    )
    dump_mapping_yaml(args.output, payload)
    print(args.output)
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    spec = load_spec(args.config)
    workspace = discover_workspace(args.workspace_root)
    payload = build_plan(spec, workspace)

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    spec = load_spec(args.config)
    workspace = discover_workspace(args.workspace_root)

    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.force:
        raise ValueError("Output directory is not empty. Use --force to overwrite contents.")

    paths = render_bundle(spec, workspace, args.output_dir)
    for path in paths:
        print(path)
    return 0


def _cmd_install_ecosystem(args: argparse.Namespace) -> int:
    python_executable = str(args.python)
    for package_name in _ECOSYSTEM_PACKAGES:
        command = [python_executable, "-m", "pip", "install"]
        if args.upgrade:
            command.append("--upgrade")
        command.append(package_name)

        print("$ " + _format_command(command))
        if not args.dry_run:
            subprocess.run(command, check=True)
    return 0


def _resolve_provider(*, visibility: str, provider: str | None) -> str:
    if provider is not None and provider.strip():
        resolved = provider.strip().lower()
    elif visibility == "public":
        resolved = "aws"
    else:
        resolved = "onprem"

    allowed = PUBLIC_PROVIDERS if visibility == "public" else PRIVATE_PROVIDERS
    if resolved not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(
            f"Provider '{resolved}' is not valid for {visibility} cloud. Allowed: {allowed_text}"
        )
    return resolved


def _resolve_default_images(workspace: WorkspaceIntegration) -> tuple[str, str]:
    # Use a minimal synthetic spec so shared image-resolution rules stay centralized.
    from refua_deploy.models import CloudTarget, DeploymentSpec, OpenClawSettings

    synthetic = DeploymentSpec(
        name="synthetic",
        cloud=CloudTarget(visibility="public", provider="aws"),
        openclaw=OpenClawSettings(base_url="https://openclaw.invalid"),
    )
    return resolve_images(synthetic, workspace)


def _format_command(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(item) for item in parts)


if __name__ == "__main__":
    raise SystemExit(main())
