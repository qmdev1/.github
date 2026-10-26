#!/usr/bin/env python3
"""Apply labels.yml to every qmdev1 repository listed in it.

Usage: python3 sync-labels.py [--apply] [--repo NAME]
Without --apply, only prints what would change. Needs `gh` logged in
with write access to the repositories, and PyYAML.
"""
import argparse
import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

import yaml

OWNER = "qmdev1"


def key(name):
    # GitHub compares label names ignoring case and accents.
    norm = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in norm if not unicodedata.combining(c))


def gh(*args, data=None):
    cmd = ["gh", "api", *args]
    if data is not None:
        cmd += ["--input", "-"]
    res = subprocess.run(cmd, input=json.dumps(data) if data is not None else None,
                         capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"gh api {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return json.loads(res.stdout) if res.stdout.strip() else None


def label_path(repo, name):
    return f"repos/{OWNER}/{repo}/labels/{quote(name, safe='')}"


def issues_with(repo, name):
    numbers, page = [], 1
    while True:
        batch = gh(f"repos/{OWNER}/{repo}/issues?state=all&per_page=100&page={page}"
                   f"&labels={quote(name, safe='')}")
        numbers += [i["number"] for i in batch]
        if len(batch) < 100:
            return numbers
        page += 1


def sync(repo, wanted, apply):
    existing = {key(l["name"]): l for l in
                gh(f"repos/{OWNER}/{repo}/labels?per_page=100")}
    used = set()

    def act(msg, fn):
        print(f"  {msg}")
        if apply:
            fn()

    for w in wanted:
        body = {"color": str(w["color"]).lstrip("#").upper(), "description": w.get("description", "")}
        current = existing.get(key(w["name"]))
        alias_keys = dict.fromkeys(key(a) for a in w.get("aliases", []))
        olds = [existing[k] for k in alias_keys if k in existing and k != key(w["name"])]
        if current is None and olds:
            first = olds.pop(0)
            act(f"rename {first['name']!r} -> {w['name']!r}",
                lambda f=first: gh("-X", "PATCH", label_path(repo, f["name"]),
                                   data={"new_name": w["name"], **body}))
            current = {**first, "name": w["name"], **body}
        elif current is None:
            act(f"create {w['name']!r}",
                lambda: gh("-X", "POST", f"repos/{OWNER}/{repo}/labels",
                           data={"name": w["name"], **body}))
            current = {"name": w["name"], **body}
        elif (current["color"].upper(), current.get("description") or "") != (body["color"], body["description"]) \
                or current["name"] != w["name"]:
            act(f"update {current['name']!r}",
                lambda c=current: gh("-X", "PATCH", label_path(repo, c["name"]),
                                     data={"new_name": w["name"], **body}))
        used.add(key(w["name"]))
        # Other aliases still present: move their issues, then delete them.
        for old in olds:
            for n in issues_with(repo, old["name"]):
                act(f"#{n}: {old['name']!r} -> {w['name']!r}",
                    lambda n=n: gh("-X", "POST", f"repos/{OWNER}/{repo}/issues/{n}/labels",
                                   data={"labels": [w["name"]]}))
            act(f"delete {old['name']!r} (merged into {w['name']!r})",
                lambda o=old: gh("-X", "DELETE", label_path(repo, o["name"])))
            used.add(key(old["name"]))
        for a in w.get("aliases", []):
            used.add(key(a))

    for k, l in existing.items():
        if k not in used:
            n = len(issues_with(repo, l["name"]))
            act(f"delete {l['name']!r} (on {n} issue(s))",
                lambda l=l: gh("-X", "DELETE", label_path(repo, l["name"])))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="make the changes")
    p.add_argument("--repo", help="only this repository")
    args = p.parse_args()
    conf = yaml.safe_load((Path(__file__).parent / "labels.yml").read_text())
    for repo in conf["repositories"]:
        if args.repo and repo != args.repo:
            continue
        print(f"== {repo}{'' if args.apply else ' (dry run)'}")
        sync(repo, conf["common"] + conf.get("per_repository", {}).get(repo, []), args.apply)


if __name__ == "__main__":
    main()
