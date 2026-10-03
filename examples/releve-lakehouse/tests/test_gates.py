"""Tests for the security-critical PII gate — the ROADMAP's rule: an anonymization strategy ships with a
test proving NO PII survives on its fixtures. We load the build-silver handler by path (its basename
`handler.py` collides with the other steps', so importlib gives it a unique module name) and exercise the
pure `_anonymize` + `RESIDUAL` scan on SYNTHETIC libellés — never real statement data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_HANDLER = Path(__file__).resolve().parent.parent / "functions" / "build_silver" / "handler.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_silver_handler", _HANDLER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # The runtime puts the bundle root, the handler's own directory, on PYTHONPATH (ADR-0089): that is where
    # the handler finds the funcd_types.py generated beside it.
    sys.path.insert(0, str(_HANDLER.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(_HANDLER.parent))
    return mod


def test_anonymize_scrubs_synthetic_pii() -> None:
    h = _load()
    # Synthetic libellés shaped like the real bank's, with fake PII — assert the sensitive parts are gone.
    cases = {
        "FACTURE(S) CARTE 4396000000002168 CARREFOUR": "4396000000002168",  # card PAN
        "VIR RECU /BEN JEAN DUPONT /REF 12345 SALAIRE": "JEAN DUPONT",  # beneficiary name
        "PRLV SEPA ID EMETTEUR/FR12ZZZ123456 URSSAF": "FR12ZZZ123456",  # creditor id
    }
    for raw, secret in cases.items():
        out = h._anonymize(raw)
        assert secret not in out, f"PII survived anonymize: {out!r}"


def test_residual_scan_flags_leftover_pii() -> None:
    h = _load()
    # A libellé that still carries a 16-digit sequence must be caught by the residual scan (→ gate raises).
    leftover = "PAIEMENT 4111111111111111 BOUTIQUE"
    hit = any(rx.search(leftover) for rx, _ in h.RESIDUAL)
    assert hit, "residual scan must flag a raw 16-digit PAN"

    clean = h._anonymize("FACTURE(S) CARTE ****CARD CARREFOUR 12,00 EUR")
    assert not any(rx.search(clean) for rx, _ in h.RESIDUAL), "clean libellé must pass the residual scan"


def test_issue_r34_handler_imports_its_generated_types() -> None:
    h = _load()
    generated = sys.modules[h.FuncOutput.__module__]
    assert generated.__file__ == str(_HANDLER.parent / "funcd_types.py")
