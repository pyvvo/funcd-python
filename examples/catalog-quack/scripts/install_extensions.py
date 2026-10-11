"""Install the DuckDB extensions catalog-quack needs into its bundle (the post-install of funcd-bundle).

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
    "v1.5.4/linux_arm64/httpfs": "6d30a487968cbe5553b7272fd5bad0e4485c4117db96e21fd1e5e2a225a5a538",
    "v1.5.4/linux_arm64/quack": "52612f68c5c7f9a5e0f4316c01b8e81d7e9ed32171473e71cff279e7a353faa7",
    "v1.5.4/linux_amd64/httpfs": "2e020c7e89b11364e413ddfab8b9ca870b1fc0a16eaf612c9445773ef68989ef",
    "v1.5.4/linux_amd64/quack": "1f3a4a1ccaf5388c7b32ec8822c40cd29b9acb3ce69936212ac6fb6e5e22cc5d",
}

ext_dir = os.path.join(os.environ["FUNCD_BUNDLE_DIR"], "duckdb-ext")
os.makedirs(ext_dir, exist_ok=True)
con = duckdb.connect()
con.execute(f"SET extension_directory='{ext_dir}'")
platform = con.execute("PRAGMA platform").fetchone()[0]
for ext in EXTENSIONS:
    con.execute(f"INSTALL {ext}")
    key = f"v{duckdb.__version__}/{platform}/{ext}"
    path = os.path.join(ext_dir, f"{key}.duckdb_extension")
    with open(path, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if PINS.get(key) != got:
        sys.exit(f"install_extensions: {key} has sha256 {got}, pinned {PINS.get(key, 'nothing')}")
