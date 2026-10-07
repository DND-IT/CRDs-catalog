"""Regenerate catalog schemas from the upstream releases listed in .github/crd-sources.yaml."""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import crd_policy

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONVERTER = ROOT / "Utilities" / "openapi2jsonschema.py"
API = "https://api.github.com"


def get(url, raw=False):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "crd-regenerate"}
    if os.environ.get("GITHUB_TOKEN") and url.startswith(API):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        body = response.read()
    return body if raw else json.loads(body)


def resolve_tag(repo, tag):
    ref = get(f"{API}/repos/{repo}/git/ref/tags/{urllib.parse.quote(tag)}")["object"]
    if ref["type"] == "tag":
        ref = get(f"{API}/repos/{repo}/git/tags/{ref['sha']}")["object"]
    return ref["sha"]


def expand(repo, sha, path):
    if not path.endswith("/"):
        return [path]
    entries = get(f"{API}/repos/{repo}/contents/{path.rstrip('/')}?ref={sha}")
    return sorted(e["path"] for e in entries if e["type"] == "file" and e["name"].endswith((".yaml", ".yml")))


def convert(raw, workdir):
    """Run the catalog converter on one CRD file and return (group, filename, schema bytes) per version."""
    by_filename = {f"{kind}_{version}.json": group for group, kind, version in crd_policy.source_identities(raw)}
    workdir = pathlib.Path(workdir)
    source, out = workdir / "crd.yaml", workdir / "out"
    out.mkdir()
    source.write_bytes(raw)
    subprocess.run(
        [sys.executable, "-I", str(CONVERTER), str(source)],
        cwd=out, env={**os.environ, "FILENAME_FORMAT": "{kind}_{version}"}, check=True, stdout=subprocess.DEVNULL,
    )
    results = []
    for schema in sorted(out.iterdir()):
        data = schema.read_bytes()
        if len(data) > crd_policy.MAX_FILE:
            raise crd_policy.PolicyError("manual-review", f"{schema.name} exceeds 2 MiB.")
        crd_policy.validate_schema(data)
        results.append((by_filename[schema.name], schema.name, data))
    return results


def regenerate(sources, root):
    rows = []
    for source in sources:
        repo, sha = source["repo"], resolve_tag(source["repo"], source["version"])
        for path in (p for entry in source["paths"] for p in expand(repo, sha, entry)):
            raw = get(f"https://raw.githubusercontent.com/{repo}/{sha}/{path}", raw=True)
            with tempfile.TemporaryDirectory() as workdir:
                for group, filename, data in convert(raw, workdir):
                    target = pathlib.Path(root, group, filename)
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(data)
                    rows.append((f"{group}/{filename}", f"https://github.com/{repo}/blob/{sha}/{path}"))
    return rows


def render_table(rows):
    lines = ["| Schema file | Source CRD |", "| --- | --- |"]
    lines += [f"| `{schema}` | {url} |" for schema, url in rows]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=pathlib.Path)
    parser.add_argument("--table", type=pathlib.Path, required=True, help="Markdown source table to write")
    parser.add_argument("--written", type=pathlib.Path, required=True, help="List of written schema paths")
    args = parser.parse_args()
    try:
        rows = regenerate(yaml.safe_load(args.sources.read_text()), ROOT)
    except crd_policy.PolicyError as exc:
        sys.exit(exc.reason)
    args.table.write_text(render_table(rows))
    args.written.write_text("".join(schema + "\n" for schema, _ in rows))
    print(f"Regenerated {len(rows)} schemas.")


if __name__ == "__main__":
    main()
