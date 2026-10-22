"""Install the DuckDB extensions releve-lakehouse needs into its bundle (the post-install of funcd-bundle).

It runs inside the build container with the bundle on PYTHONPATH, so the vendored duckdb downloads the
extensions built for the target platform. At runtime the handler points DuckDB at the same directory
through FUNCD_BUNDLE_DIR (ADR-0089).
"""

import hashlib
import os
import sys

import duckdb

EXTENSIONS = ("quack", "httpfs")

# SHA-256 of each extension file DuckDB downloads, keyed "<duckdb version>/<platform>/<extension>". DuckDB
# rebuilds them now and then under one version, so a bundle is reproducible only if each file is checked.
PINS = {
    "v2.0.0/linux_arm64/httpfs": "53f7aea1a0448ea62ef9b63f95da37c5766070084f14f14110e275e76382e7b2",
    "v2.0.0/linux_arm64/quack": "f3e30e7533f53e2b935ec29b59f87cb26ec3afaacb9880e9072426a6d16ced9e",
    "v2.0.0/linux_amd64/httpfs": "77e6c429aa04f25da7962572f12b19113e0ce46080fa1365ad66017aadcdb68d",
    "v2.0.0/linux_amd64/quack": "9c893bd543ec2c5253d0c47d5614f90a1bfa1791c735dc6bbcea4e8bd3811a97",
}

ext_dir = os.path.join(os.environ["FUNCD_BUNDLE_DIR"], "duckdb-ext")
os.makedirs(ext_dir, exist_ok=True)
con = duckdb.connect()
con.execute(f"SET extension_directories=['{ext_dir}']")
platform = con.execute("PRAGMA platform").fetchone()[0]
for ext in EXTENSIONS:
    con.execute(f"INSTALL {ext}")
    key = f"v{duckdb.__version__}/{platform}/{ext}"
    path = os.path.join(ext_dir, f"{key}.duckdb_extension")
    with open(path, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if PINS.get(key) != got:
        sys.exit(f"install_extensions: {key} has sha256 {got}, pinned {PINS.get(key, 'nothing')}")
