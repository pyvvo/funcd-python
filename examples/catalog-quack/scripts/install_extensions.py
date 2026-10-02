"""Install the DuckDB extensions catalog-quack needs into its bundle (the post-install of funcd-bundle).

It runs inside the build container with the bundle on PYTHONPATH, so the vendored duckdb downloads the
extensions built for the target platform. At runtime the handler points DuckDB at the same directory
through FUNCD_BUNDLE_DIR (ADR-0089).
"""

import os

import duckdb

EXTENSIONS = ("quack", "httpfs")

ext_dir = os.path.join(os.environ["FUNCD_BUNDLE_DIR"], "duckdb-ext")
os.makedirs(ext_dir, exist_ok=True)
con = duckdb.connect()
con.execute(f"SET extension_directory='{ext_dir}'")
for ext in EXTENSIONS:
    con.execute(f"INSTALL {ext}")
