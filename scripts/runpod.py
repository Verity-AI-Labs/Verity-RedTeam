"""RunPod GPUs for training: create a pod with SSH, wait for it, and print how to reach it.

    python scripts/runpod.py create shop-baseline        # an H100/A100 80GB pod; prints ssh root@IP -p PORT
    python scripts/runpod.py list
    python scripts/runpod.py delete POD_ID               # stops billing; copy results off first

Needs RUNPOD_API_KEY in .env and an SSH public key in ~/.ssh. scripts/pod.sh sets a pod up and trains on it.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://rest.runpod.io/v1"
GPUS = ["NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe", "NVIDIA A100-SXM4-80GB", "NVIDIA A100 80GB PCIe"]
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"


def key():
    for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").is_file() else []:
        if line.startswith("RUNPOD_API_KEY="):
            os.environ.setdefault("RUNPOD_API_KEY", line.split("=", 1)[1].strip().strip("'\""))
    if not os.environ.get("RUNPOD_API_KEY"):
        sys.exit("RUNPOD_API_KEY is not set (add it to .env)")
    return os.environ["RUNPOD_API_KEY"]


def call(method, path, body=None):
    request = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        sys.exit(f"RunPod {method} {path}: HTTP {error.code}: {error.read().decode()[:800]}")


def ssh(pod):
    port = (pod.get("portMappings") or {}).get("22")
    return f"ssh -o StrictHostKeyChecking=accept-new root@{pod['publicIp']} -p {port}" if pod.get("publicIp") and port else None


def create(name, gpus, disk):
    public = next(iter(sorted((Path.home() / ".ssh").glob("id_*.pub"))), None)
    if public is None:
        sys.exit("No SSH public key in ~/.ssh")
    pod = call("POST", "/pods", {"name": name, "imageName": IMAGE, "gpuTypeIds": gpus, "gpuCount": 1,
                                 "containerDiskInGb": disk, "volumeInGb": 0, "ports": ["22/tcp"],
                                 "env": {"PUBLIC_KEY": public.read_text().strip()}, "cloudType": "SECURE",
                                 "supportPublicIp": True})
    print(f"Created {pod['id']} ({name}); waiting for SSH...", flush=True)
    for _ in range(90):
        pod = call("GET", f"/pods/{pod['id']}")
        if ssh(pod):
            print(json.dumps({"id": pod["id"], "name": name, "gpu": (pod.get("machine") or {}).get("gpuTypeId")
                              or pod.get("gpuTypeId"), "cost_per_hr": pod.get("costPerHr"), "ssh": ssh(pod)}))
            return
        time.sleep(10)
    sys.exit(f"Pod {pod['id']} has no SSH address after 15 minutes: {json.dumps(pod)[:800]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    made = commands.add_parser("create")
    made.add_argument("name")
    made.add_argument("--gpu", action="append", help=f"GPU type, in order of preference (default: {', '.join(GPUS)})")
    made.add_argument("--disk", type=int, default=150, help="container disk, GB (model, venv, checkpoints)")
    commands.add_parser("list")
    deleted = commands.add_parser("delete")
    deleted.add_argument("pod")
    args = parser.parse_args()
    if args.command == "create":
        create(args.name, args.gpu or GPUS, args.disk)
    elif args.command == "list":
        for pod in call("GET", "/pods"):
            print(json.dumps({k: pod.get(k) for k in ("id", "name", "desiredStatus", "costPerHr")} | {"ssh": ssh(pod)}))
    else:
        call("DELETE", f"/pods/{args.pod}")
        print(f"Deleted {args.pod}")


if __name__ == "__main__":
    main()
