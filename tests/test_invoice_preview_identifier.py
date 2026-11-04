"""Reserved preview invoice numbers must never reach real invoice creation."""
import pytest
from subsystems.invoice.model import validate


def payload(identifier):
    return {
        'client': {'name': 'Synthetic client', 'billing_address': '100 Test Street', 'email': 'test@example.invalid'},
        'job': {'name': 'Synthetic project', 'project_number': 'PRO-2026-1104-001'},
        'invoice': {'identifier': identifier, 'issue_date': '2026-11-04', 'payment_terms': 'Net 30',
                    'line_items': [{'description': 'Test work', 'amount': 125}]},
    }


@pytest.mark.parametrize('identifier', ['INV-PREVIEW-20261104-120000', ' inv-preview-20261104-120000 '])
def test_live_validation_rejects_reserved_preview_number(identifier):
    with pytest.raises(ValueError, match='Preview-only invoice identifier'):
        validate(payload(identifier))


def test_blank_invoice_number_uses_project_spine():
    data = payload('')
    validate(data)
    assert data['invoice']['identifier'] == 'INV-2026-1104-001'


def test_explicit_legacy_invoice_number_still_works():
    data = payload('GVC-2026-C-123')
    validate(data)
    assert data['invoice']['identifier'] == 'GVC-2026-C-123'
