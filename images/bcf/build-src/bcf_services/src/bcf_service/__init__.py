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

"""FileEngine BCF-API subservice (Phase F / §12 of the xeokit upgrade/BCF plan).

The BCF (BIM Collaboration Format) protocol door: a FastAPI adapter that lets
external AEC tools (Revit/Navisworks/Solibri/BIMcollab) collaborate live against
FileEngine over BCF-API 2.1. It is *not* a second issue store — topics and
comments live in the discussion substrate (reached through the shared
``comment_store`` interface); this service owns only the BCF projection tables.
"""

__version__ = "0.1.0"
