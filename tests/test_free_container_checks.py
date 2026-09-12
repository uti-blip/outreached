"""Keep the mutating CI probe confined to disposable local targets."""

import pytest

from scripts.verify_free_container import VerificationError, WorkspaceClient, snapshot_digest


@pytest.mark.parametrize(
    "address",
    [
        "https://workspace.example.com",
        "http://workspace.example.com",
        "http://user:password@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?target=remote",
    ],
)
def test_synthetic_probe_rejects_nonlocal_or_ambiguous_targets(address):
    with pytest.raises(VerificationError, match="loopback"):
        WorkspaceClient(address)


def test_snapshot_comparison_ignores_row_order_but_detects_data_loss():
    first = {"leads": [{"id": 1, "status": "do_not_contact"}, {"id": 2, "status": "new"}]}
    reordered = {"leads": list(reversed(first["leads"]))}
    missing = {"leads": first["leads"][:1]}
    assert snapshot_digest(first) == snapshot_digest(reordered)
    assert snapshot_digest(first) != snapshot_digest(missing)
