"""Install the DuckDB extensions lake-quack needs into its bundle (the post-install of funcd-bundle).

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
    "v1.5.6/linux_arm64/httpfs": "ae940d800b7f938a4848d0eebcffb28dda0bbed76987f11dc55faa6d1a86db0d",
    "v1.5.6/linux_arm64/quack": "618af63cf01e07c396fca03cc887d8784658347f0fc346e426a2fec12f66b421",
    "v1.5.6/linux_amd64/httpfs": "ff36e7d2561e21b958c243c6bd5f6a10cfc055a98ea76c0280b94728b57588e2",
    "v1.5.6/linux_amd64/quack": "efd924d2a87b0cbcdda8a7418aa1daf33e0267388c87817d2e622b0a918c0800",
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
