#!/usr/bin/env python3

import gzip
import hashlib
import os
import subprocess

PACKAGE_DIR = "pool/main/t/termux-ai"
OUTPUT_DIR = "dists/stable/main/binary-all"

os.makedirs(OUTPUT_DIR, exist_ok=True)

packages = []

for filename in os.listdir(PACKAGE_DIR):
    if not filename.endswith(".deb"):
        continue

    path = os.path.join(PACKAGE_DIR, filename)

    result = subprocess.run(
        ["dpkg-deb", "-f", path],
        capture_output=True,
        text=True,
        check=True
    )

    fields = {}
    current = None

    for line in result.stdout.splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            current = key
            fields[current] = value.strip()
        elif line.startswith(" ") and current:
            fields[current] += "\n" + line

    size = os.path.getsize(path)

    with open(path, "rb") as f:
        data = f.read()

    sha256 = hashlib.sha256(data).hexdigest()

    relative_path = os.path.relpath(path, ".")

    entry = (
        f"Package: {fields.get('Package', '')}\n"
        f"Version: {fields.get('Version', '')}\n"
        f"Architecture: {fields.get('Architecture', '')}\n"
        f"Maintainer: {fields.get('Maintainer', '')}\n"
        f"Description: {fields.get('Description', '')}\n"
        f"Filename: {relative_path}\n"
        f"Size: {size}\n"
        f"SHA256: {sha256}\n"
        f"\n"
    )

    packages.append(entry)

packages_text = "".join(packages)

packages_file = os.path.join(OUTPUT_DIR, "Packages")

with open(packages_file, "w", encoding="utf-8") as f:
    f.write(packages_text)

with open(packages_file, "rb") as f:
    data = f.read()

with gzip.open(packages_file + ".gz", "wb", compresslevel=9) as f:
    f.write(data)

print("Packages index created successfully.")
print(f"Packages: {packages_file}")
print(f"Packages.gz: {packages_file}.gz")
