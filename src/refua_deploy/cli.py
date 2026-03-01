from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

    init_parser = subparsers.add_parser(
        "init", help="Generate a starter deployment config"
    )
    init_parser.add_argument(
        "--output", type=Path, required=True, help="Output YAML config path"
    )
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

    plan_parser = subparsers.add_parser(
        "plan", help="Validate a config and emit deployment plan"
    )
    plan_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
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
    render_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
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

    apply_parser = subparsers.add_parser(
        "apply",
        help="Render artifacts and apply deployment runtime commands",
    )
    apply_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
    apply_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    apply_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Rendered artifact directory (default: <config-dir>/dist/<name>)",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print runtime commands without executing them",
    )
    apply_parser.set_defaults(handler=_cmd_apply)

    destroy_parser = subparsers.add_parser(
        "destroy",
        help="Render artifacts and tear down runtime resources",
    )
    destroy_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
    destroy_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    destroy_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Rendered artifact directory (default: <config-dir>/dist/<name>)",
    )
    destroy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print runtime commands without executing them",
    )
    destroy_parser.set_defaults(handler=_cmd_destroy)

    status_parser = subparsers.add_parser(
        "status",
        help="Show deployment status for rendered runtime targets",
    )
    status_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
    status_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    status_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Rendered artifact directory (default: <config-dir>/dist/<name>)",
    )
    status_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print runtime commands without executing them",
    )
    status_parser.set_defaults(handler=_cmd_status)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run deployment preflight diagnostics",
    )
    doctor_parser.add_argument(
        "--config", type=Path, required=True, help="Deployment config file"
    )
    doctor_parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional workspace root for local project integration discovery",
    )
    doctor_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Rendered artifact directory (default: <config-dir>/dist/<name>)",
    )
    doctor_parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return non-zero when any diagnostic check fails",
    )
    doctor_parser.set_defaults(handler=_cmd_doctor)

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
        raise ValueError(
            "Output directory is not empty. Use --force to overwrite contents."
        )

    paths = render_bundle(spec, workspace, args.output_dir)
    for path in paths:
        print(path)
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    spec, workspace, output_dir = _load_runtime_context(args)
    render_bundle(spec, workspace, output_dir)
    commands = _lifecycle_commands(spec, output_dir, action="apply")
    if not commands:
        _print_single_machine_guidance(output_dir)
        return 0

    for command in commands:
        _run_runtime_command(command, dry_run=args.dry_run)
    return 0


def _cmd_destroy(args: argparse.Namespace) -> int:
    spec, workspace, output_dir = _load_runtime_context(args)
    render_bundle(spec, workspace, output_dir)
    commands = _lifecycle_commands(spec, output_dir, action="destroy")
    if not commands:
        _print_single_machine_destroy_guidance(output_dir)
        return 0

    for command in commands:
        _run_runtime_command(command, dry_run=args.dry_run)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    spec, workspace, output_dir = _load_runtime_context(args)
    render_bundle(spec, workspace, output_dir)
    commands = _lifecycle_commands(spec, output_dir, action="status")
    if commands:
        for command in commands:
            _run_runtime_command(command, dry_run=args.dry_run)
        return 0

    payload = _single_machine_status_payload(output_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    spec, workspace, output_dir = _load_runtime_context(args)
    render_bundle(spec, workspace, output_dir)

    checks = [
        _doctor_check(
            "python",
            Path(sys.executable).exists(),
            f"python executable: {sys.executable}",
        ),
        _doctor_check(
            "workspace_projects_detected",
            True,
            f"detected {len(workspace.projects)} local workspace projects",
        ),
    ]

    if spec.uses_kubernetes:
        checks.append(
            _doctor_check(
                "kubectl_available",
                shutil.which("kubectl") is not None,
                "kubectl is required for kubernetes apply/destroy/status",
            )
        )
    elif spec.uses_compose:
        checks.append(
            _doctor_check(
                "docker_available",
                shutil.which("docker") is not None,
                "docker compose is required for compose apply/destroy/status",
            )
        )
    else:
        checks.extend(_single_machine_doctor_checks(output_dir))

    payload = {
        "healthy": all(check["ok"] for check in checks),
        "orchestrator": spec.runtime.orchestrator,
        "output_dir": str(output_dir),
        "checks": checks,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    if args.fail_on_error and not payload["healthy"]:
        return 1
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


def _resolve_output_dir(
    *,
    config_path: Path,
    deployment_name: str,
    output_dir: Path | None,
) -> Path:
    if output_dir is not None:
        return output_dir.resolve()
    return config_path.resolve().parent / "dist" / deployment_name


def _load_runtime_context(
    args: argparse.Namespace,
) -> tuple[Any, WorkspaceIntegration, Path]:
    spec = load_spec(args.config)
    workspace = discover_workspace(args.workspace_root)
    output_dir = _resolve_output_dir(
        config_path=args.config,
        deployment_name=spec.name,
        output_dir=args.output_dir,
    )
    return spec, workspace, output_dir


def _compose_env_file(private_dir: Path) -> Path:
    env_path = private_dir / ".env"
    if env_path.exists():
        return env_path
    return private_dir / ".env.template"


def _lifecycle_commands(
    spec: Any,
    output_dir: Path,
    *,
    action: str,
) -> list[list[str]]:
    if spec.uses_kubernetes:
        kustomize_dir = output_dir / "kubernetes"
        if action == "apply":
            return [["kubectl", "apply", "-k", str(kustomize_dir)]]
        if action == "destroy":
            return [
                [
                    "kubectl",
                    "delete",
                    "-k",
                    str(kustomize_dir),
                    "--ignore-not-found=true",
                ]
            ]
        if action == "status":
            return [["kubectl", "get", "all", "-n", spec.runtime.namespace]]

    if spec.uses_compose:
        private_dir = output_dir / "private"
        compose_path = private_dir / "docker-compose.yaml"
        env_file = _compose_env_file(private_dir)
        base = [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "--env-file",
            str(env_file),
        ]
        if action == "apply":
            return [base + ["up", "-d"]]
        if action == "destroy":
            return [base + ["down", "--remove-orphans"]]
        if action == "status":
            return [base + ["ps"]]

    return []


def _run_runtime_command(command: Sequence[str], *, dry_run: bool) -> None:
    print("$ " + _format_command(command))
    if not dry_run:
        subprocess.run(list(command), check=True)


def _doctor_check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
    }


def _single_machine_status_payload(output_dir: Path) -> dict[str, Any]:
    single_dir = output_dir / "single-machine"
    scripts = {
        name: (single_dir / name).exists()
        for name in (
            "install-ecosystem.sh",
            "run-mcp.sh",
            "run-campaign.sh",
            "run-studio.sh",
        )
    }
    env_files = {
        ".env": (single_dir / ".env").exists(),
        ".env.template": (single_dir / ".env.template").exists(),
    }
    return {
        "orchestrator": "single-machine",
        "single_machine_dir": str(single_dir),
        "scripts": scripts,
        "env_files": env_files,
    }


def _single_machine_doctor_checks(output_dir: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    single_dir = output_dir / "single-machine"
    env_template_path = single_dir / ".env.template"
    run_studio_path = single_dir / "run-studio.sh"

    checks.append(
        _doctor_check(
            "bash_available",
            shutil.which("bash") is not None,
            "bash is required to run generated single-machine scripts",
        )
    )
    checks.append(
        _doctor_check(
            "single_machine_env_template_exists",
            env_template_path.exists(),
            f"expected {env_template_path}",
        )
    )
    checks.append(
        _doctor_check(
            "single_machine_run_studio_script_exists",
            run_studio_path.exists(),
            f"expected {run_studio_path}",
        )
    )

    env_text = env_template_path.read_text(encoding="utf-8") if env_template_path.exists() else ""
    run_studio_text = (
        run_studio_path.read_text(encoding="utf-8") if run_studio_path.exists() else ""
    )

    for token_key in (
        "REFUA_STUDIO_AUTH_TOKENS",
        "REFUA_STUDIO_OPERATOR_TOKENS",
        "REFUA_STUDIO_ADMIN_TOKENS",
        "REFUA_MCP_AUTH_TOKENS",
    ):
        checks.append(
            _doctor_check(
                f"single_machine_env_has_{token_key.lower()}",
                token_key in env_text,
                f"{token_key} placeholder present in single-machine/.env.template",
            )
        )

    checks.append(
        _doctor_check(
            "run_studio_supports_auth_tokens",
            "--auth-token" in run_studio_text
            and "--operator-token" in run_studio_text
            and "--admin-token" in run_studio_text,
            "run-studio.sh passes Studio auth token flags when configured",
        )
    )
    return checks


def _print_single_machine_guidance(output_dir: Path) -> None:
    single_dir = output_dir / "single-machine"
    print(f"Rendered single-machine bundle at: {single_dir}")
    print(
        "No automatic 'apply' command for single-machine mode. "
        "Use run-mcp.sh/run-studio.sh/run-campaign.sh explicitly."
    )


def _print_single_machine_destroy_guidance(output_dir: Path) -> None:
    single_dir = output_dir / "single-machine"
    print(f"Single-machine bundle located at: {single_dir}")
    print(
        "No automatic 'destroy' action for single-machine mode. "
        "Stop any manually started processes and remove artifacts if needed."
    )


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
