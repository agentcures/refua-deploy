from __future__ import annotations

from pathlib import Path

from refua_deploy.config import spec_from_mapping
from refua_deploy.integration import discover_workspace, resolve_images


def test_discover_workspace_and_resolve_images(tmp_path: Path) -> None:
    workspace_root = tmp_path / "refua-project"
    campaign_dir = workspace_root / "ClawCures"
    mcp_dir = workspace_root / "refua-mcp"
    campaign_dir.mkdir(parents=True)
    mcp_dir.mkdir(parents=True)

    (campaign_dir / "pyproject.toml").write_text(
        """
[tool.poetry]
name = "ClawCures"
version = "0.2.1"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (mcp_dir / "pyproject.toml").write_text(
        """
[project]
name = "refua-mcp"
version = "0.7.5"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    workspace = discover_workspace(workspace_root)

    assert workspace.root == workspace_root
    assert workspace.projects["ClawCures"].version == "0.2.1"
    assert workspace.projects["refua-mcp"].version == "0.7.5"

    spec = spec_from_mapping(
        {
            "name": "my-campaign",
            "cloud": {"visibility": "public", "provider": "aws"},
            "openclaw": {"base_url": "https://openclaw.example.org"},
        }
    )

    campaign_image, mcp_image = resolve_images(spec, workspace)
    assert campaign_image.endswith("ClawCures:0.2.1")
    assert mcp_image.endswith("refua-mcp:0.7.5")


def test_discover_workspace_includes_extended_refua_projects(tmp_path: Path) -> None:
    workspace_root = tmp_path / "refua-project"
    project_names = (
        "refua-clinical",
        "refua-preclinical",
        "refua-data",
        "refua-regulatory",
        "refua-studio",
    )
    for name in project_names:
        project_dir = workspace_root / name
        project_dir.mkdir(parents=True)
        (project_dir / "pyproject.toml").write_text(
            (
                "\n".join(
                    [
                        "[project]",
                        f'name = "{name}"',
                        'version = "1.2.3"',
                    ]
                )
            )
            + "\n",
            encoding="utf-8",
        )

    workspace = discover_workspace(workspace_root)

    for name in project_names:
        assert name in workspace.projects
        assert workspace.projects[name].version == "1.2.3"
