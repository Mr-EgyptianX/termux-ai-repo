#!/usr/bin/env python3

import gzip
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(".")
POOL = ROOT / "pool/main/t/termux-ai"
DIST = ROOT / "dists/stable"
PACKAGE_DIRS = [
    DIST / "main/binary-arm",
    DIST / "main/binary-all",
]

deb_files = sorted(POOL.glob("*.deb"))

if not deb_files:
    raise SystemExit("ERROR: No .deb package found.")

def deb_field(deb, field):
    return subprocess.check_output(
        ["dpkg-deb", "-f", str(deb), field],
        text=True
    ).strip()

entries = []

for deb in deb_files:
    size = deb.stat().st_size
    sha256 = hashlib.sha256(deb.read_bytes()).hexdigest()
    filename = deb.relative_to(ROOT).as_posix()

    entry = (
        f"Package: {deb_field(deb, 'Package')}\n"
        f"Version: {deb_field(deb, 'Version')}\n"
        f"Architecture: {deb_field(deb, 'Architecture')}\n"
        f"Maintainer: {deb_field(deb, 'Maintainer')}\n"
        f"Description: {deb_field(deb, 'Description')}\n"
        f"Filename: {filename}\n"
        f"Size: {size}\n"
        f"SHA256: {sha256}\n"
    )

    entries.append(entry)

packages_text = "\n".join(entries) + "\n"

for directory in PACKAGE_DIRS:
    directory.mkdir(parents=True, exist_ok=True)

    packages = directory / "Packages"
    packages_gz = directory / "Packages.gz"

    packages.write_text(packages_text, encoding="utf-8")

    with gzip.open(packages_gz, "wb", compresslevel=9) as f:
        f.write(packages_text.encode("utf-8"))

release_files = [
    "main/binary-arm/Packages",
    "main/binary-arm/Packages.gz",
    "main/binary-all/Packages",
    "main/binary-all/Packages.gz",
]

date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")

release_lines = [
    "Origin: Mr-EgyptianX",
    "Label: Termux AI",
    "Suite: stable",
    "Codename: stable",
    f"Date: {date}",
    "Architectures: arm all",
    "Components: main",
    "Description: Termux AI APT Repository",
    "",
    "SHA256:",
]

for relative in release_files:
    path = DIST / relative
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    size = len(data)

    release_lines.append(f" {sha256} {size:18d} {relative}")

release_lines.append("")

(DIST / "Release").write_text(
    "\n".join(release_lines),
    encoding="utf-8"
)

print("======================================")
print(" Termux AI Repository Updated")
print("======================================")
print(f"Packages: {len(deb_files)}")
print(f"Date:     {date}")
print("Indexes:  ARM + ALL")
print("Hashes:   SHA256 updated")
print("Release:  updated")
print("======================================")

subprocess.run(["git", "add", "."], check=True)

status = subprocess.run(
    ["git", "diff", "--cached", "--quiet"]
)

if status.returncode != 0:
    subprocess.run(
        ["git", "commit", "-m", "Update Termux AI APT repository"],
        check=True
    )
    subprocess.run(["git", "push", "origin", "main"], check=True)
    print("GitHub:   pushed successfully")
else:
    print("GitHub:   no changes to push")

print("DONE.")
