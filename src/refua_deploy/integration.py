from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refua_deploy.models import DeploymentSpec

_DEFAULT_IMAGE_PREFIX = "ghcr.io/agentcures"
_PROJECT_NAMES = ("ClawCures", "refua-mcp")


@dataclass(slots=True)
class ProjectReference:
    name: str
    path: Path
    version: str | None

    @property
    def image(self) -> str:
        tag = self.version if self.version else "latest"
        return f"{_DEFAULT_IMAGE_PREFIX}/{self.name}:{tag}"


@dataclass(slots=True)
class WorkspaceIntegration:
    root: Path
    projects: dict[str, ProjectReference] = field(default_factory=dict)


def discover_workspace(root: str | Path | None = None) -> WorkspaceIntegration:
    search_start = Path(root).resolve() if root is not None else Path.cwd().resolve()
    candidate_roots = [search_start, search_start.parent]

    best_root = search_start
    best_count = -1
    best_projects: dict[str, ProjectReference] = {}

    for candidate in candidate_roots:
        projects: dict[str, ProjectReference] = {}
        for project_name in _PROJECT_NAMES:
            project_dir = candidate / project_name
            pyproject_path = project_dir / "pyproject.toml"
            if not pyproject_path.exists():
                continue
            version = _read_version(pyproject_path)
            projects[project_name] = ProjectReference(
                name=project_name,
                path=project_dir,
                version=version,
            )

        if len(projects) > best_count:
            best_count = len(projects)
            best_root = candidate
            best_projects = projects

    return WorkspaceIntegration(root=best_root, projects=best_projects)


def resolve_images(spec: DeploymentSpec, workspace: WorkspaceIntegration) -> tuple[str, str]:
    campaign_image = spec.runtime.campaign.image
    if not campaign_image:
        campaign_ref = workspace.projects.get("ClawCures")
        if campaign_ref:
            campaign_image = campaign_ref.image
        else:
            campaign_image = f"{_DEFAULT_IMAGE_PREFIX}/ClawCures:latest"

    mcp_image = spec.runtime.mcp.image
    if not mcp_image:
        mcp_ref = workspace.projects.get("refua-mcp")
        if mcp_ref:
            mcp_image = mcp_ref.image
        else:
            mcp_image = f"{_DEFAULT_IMAGE_PREFIX}/refua-mcp:latest"

    return campaign_image, mcp_image


def integration_payload(workspace: WorkspaceIntegration) -> dict[str, Any]:
    projects = [
        {
            "name": ref.name,
            "path": str(ref.path),
            "version": ref.version,
            "image": ref.image,
        }
        for ref in sorted(workspace.projects.values(), key=lambda item: item.name)
    ]
    return {
        "workspace_root": str(workspace.root),
        "projects": projects,
    }


def _read_version(pyproject_path: Path) -> str | None:
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = payload.get("project")
    if isinstance(project, Mapping):
        version = project.get("version")
        if version is not None:
            version_text = str(version).strip()
            if version_text:
                return version_text

    tool = payload.get("tool")
    if isinstance(tool, Mapping):
        poetry = tool.get("poetry")
        if isinstance(poetry, Mapping):
            version = poetry.get("version")
            if version is not None:
                version_text = str(version).strip()
                if version_text:
                    return version_text
    return None
