# Copyright (C) 2026 James Hickman
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Per-tenant schema naming + BCF projection DDL — hermetic (no DB)."""
import pytest

from bcf_service.store import schema_name, tenant_ddl


@pytest.mark.parametrize("tenant,expected", [
    ("", "tenant_default"),
    (None, "tenant_default"),
    ("default", "tenant_default"),
    ("acme", "tenant_acme"),
    ("a-b.c d", "tenant_a_b_c_d"),
])
def test_schema_name(tenant, expected):
    assert schema_name(tenant) == expected


def test_tenant_ddl_covers_the_bcf_projection_tables():
    ddl = tenant_ddl("acme")
    for table in ("bcf_project", "bcf_topic", "bcf_viewpoint", "bcf_guid_map"):
        assert f'"tenant_acme".{table}' in ddl, table
    # Round-trip identity + 3.0 forward-compat fields are present.
    assert "bcf_guid" in ddl
    assert "server_assigned_id" in ddl
    # Idempotent form (safe to run on every startup).
    assert "CREATE TABLE IF NOT EXISTS" in ddl
