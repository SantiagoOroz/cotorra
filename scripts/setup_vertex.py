#!/usr/bin/env python3
"""Point Cotorra at Vertex AI, end to end.

    gcloud auth login
    gcloud auth application-default login
    python scripts/setup_vertex.py

Creates (or reuses) a Google Cloud project, links it to a billing account,
enables the Vertex AI API, writes the settings into .env, and makes one real
transcription call to prove it works.

Why bother, when an API key is one click: an AI Studio key on the free tier
allows roughly 15-20 requests per DAY per model. Vertex AI is quota per
minute, and it is where Google Cloud credits — hackathon, GDP, startup — are
actually spendable. Same models, same code path, same everything downstream.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def gcloud_path() -> str:
    """Find gcloud, including copies that were unzipped rather than installed."""
    found = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if found:
        return found
    for candidate in [
        Path("C:/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"),
        Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd",
        Path.home() / "google-cloud-sdk/bin/gcloud",
    ]:
        if candidate.exists():
            return str(candidate)
    sys.exit(
        "gcloud is not on PATH.\n"
        "  Install it: https://cloud.google.com/sdk/docs/install\n"
        "  Then:       gcloud auth login && gcloud auth application-default login"
    )


GCLOUD = None


def run(*args: str, check: bool = True, quiet: bool = False) -> str:
    cmd = [GCLOUD, *args]
    if not quiet:
        print(f"  $ gcloud {' '.join(args)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        sys.exit("  failed: " + (detail[-1] if detail else "unknown error"))
    return result.stdout.strip()


def require_auth() -> str:
    accounts = run("auth", "list", "--format=value(account)", quiet=True, check=False)
    active = [a for a in accounts.splitlines() if a.strip()]
    if not active:
        sys.exit(
            "Not signed in. Run these two (the second one is what Vertex needs):\n"
            "  gcloud auth login\n"
            "  gcloud auth application-default login"
        )
    return active[0]


def pick_billing_account() -> str:
    raw = run("billing", "accounts", "list", "--format=json", quiet=True, check=False)
    try:
        accounts = [a for a in json.loads(raw or "[]") if a.get("open")]
    except json.JSONDecodeError:
        accounts = []
    if not accounts:
        sys.exit(
            "No open billing account found.\n"
            "  If you claimed credits, the billing account can take a few minutes to\n"
            "  appear. Check https://console.cloud.google.com/billing and re-run.\n"
            "  Without billing, Vertex AI will refuse every request."
        )
    if len(accounts) > 1:
        print("\n  More than one billing account:")
        for a in accounts:
            print(f"    {a['name'].split('/')[-1]}  {a.get('displayName', '')}")
        print("  Using the first. Pass --billing to choose.\n")
    return accounts[0]["name"].split("/")[-1]


def ensure_project(project_id: str) -> None:
    existing = run("projects", "list", f"--filter=projectId={project_id}",
                   "--format=value(projectId)", quiet=True, check=False)
    if existing.strip() == project_id:
        print(f"  project {project_id} already exists")
        return
    run("projects", "create", project_id, "--name", "Cotorra live captions")


def main() -> int:
    global GCLOUD
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="", help="Project id. Default: a new cotorra-<random> one")
    ap.add_argument("--billing", default="", help="Billing account id. Default: the first open one")
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--no-env", action="store_true", help="Print settings instead of writing .env")
    args = ap.parse_args()

    GCLOUD = gcloud_path()
    print(f"\ngcloud: {GCLOUD}\n")

    account = require_auth()
    print(f"signed in as {account}\n")

    project = args.project or f"cotorra-{secrets.token_hex(3)}"
    if not re.fullmatch(r"[a-z][a-z0-9-]{5,29}", project):
        sys.exit(f"'{project}' is not a valid project id (lowercase, 6-30 chars, starts a letter)")

    print("1. project")
    ensure_project(project)

    print("\n2. billing")
    billing = args.billing or pick_billing_account()
    run("billing", "projects", "link", project, "--billing-account", billing)

    print("\n3. Vertex AI API")
    run("services", "enable", "aiplatform.googleapis.com", "--project", project)

    print("\n4. quota project for Application Default Credentials")
    run("auth", "application-default", "set-quota-project", project, check=False)

    settings = {
        "COTORRA_ENGINE": "gemini",
        "GEMINI_USE_VERTEX": "true",
        "GCP_PROJECT": project,
        "GCP_LOCATION": args.location,
        "GEMINI_MODEL": args.model,
    }

    if args.no_env:
        print("\nAdd these to .env:\n")
        for k, v in settings.items():
            print(f"  {k}={v}")
    else:
        env = REPO / ".env"
        lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
        for key, value in settings.items():
            for i, line in enumerate(lines):
                if line.startswith(key + "="):
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.append(f"{key}={value}")
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n5. wrote {env}")
        for k, v in settings.items():
            print(f"     {k}={v}")

    print("\n" + "=" * 68)
    print("  Now prove it works:")
    print("    cotorra doctor --live")
    print("\n  Then caption two real talks:")
    print("    python scripts/fetch_samples.py")
    print("    MANIFEST=demo.yaml ADMIN_TOKEN=demo cotorra serve")
    print("=" * 68 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
