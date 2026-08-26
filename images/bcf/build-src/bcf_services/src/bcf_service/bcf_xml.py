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

"""BCF-XML (``.bcfzip``) serialization codec — Phase E / §11.

The pure, reusable core of BCF-XML round-trip: convert between our internal issue
model (a dict shaped like a discussion thread + its ``anchor.viewpoint`` + comments)
and the buildingSMART **BCF 2.1** file format. No DB, no HTTP — the import/export
endpoints and (Phase F) the live API both build on this.

Internal issue shape (what the store will produce / consume)::

    {
      "guid": "<uuid>",                      # BCF Topic Guid (round-trip identity)
      "title": "...", "topic_type": "Issue", "topic_status": "Open",
      "priority": "Normal", "creation_date": "2026-01-02T03:04:05Z",
      "creation_author": "alice",
      "comments": [
        {"guid": "<uuid>", "date": "...", "author": "...", "comment": "text",
         "viewpoint_guid": "<vp-guid>"|None},
      ],
      "viewpoints": [
        {"guid": "<vp-guid>",
         "viewpoint": { <BCF-API viewpoint JSON, as xeokit getViewpoint() emits> },
         "snapshot": b"...png..."|None},
      ],
    }

The ``viewpoint`` sub-object is the BCF-API JSON form (``perspective_camera`` /
``orthogonal_camera``, ``clipping_planes``, ``components``) — the same shape the
frontend anchor stores — so JSON↔.bcfv is a near-identity mapping.

Caveats carried from the plan: BCF Topic/Comment/Viewpoint GUIDs are ordinary
UUIDs, while ``Component.IfcGuid`` is the native 22-char compressed IFC GlobalId
(§16 — do not conflate). BCF coordinates are metres (§16) — the codec does not
scale; callers hand it metre-space viewpoints. XML is parsed with the stdlib
parser; hardening against hostile XML (defusedxml) is a Phase F follow-on.
"""
from __future__ import annotations

import io
import zipfile
from typing import List, Optional
from xml.etree import ElementTree as ET

BCF_VERSION = "2.1"


# --------------------------------------------------------------------------- #
# Small XML helpers
# --------------------------------------------------------------------------- #

def _text(parent: ET.Element, tag: str, value) -> None:
    if value is None:
        return
    el = ET.SubElement(parent, tag)
    el.text = str(value)


def _vec3(parent: ET.Element, tag: str, v: Optional[dict]) -> None:
    """Append ``<tag><X/><Y/><Z/></tag>`` from a ``{x,y,z}`` dict."""
    if not isinstance(v, dict):
        return
    el = ET.SubElement(parent, tag)
    _text(el, "X", v.get("x"))
    _text(el, "Y", v.get("y"))
    _text(el, "Z", v.get("z"))


def _read_vec3(el: Optional[ET.Element]) -> Optional[dict]:
    if el is None:
        return None
    out = {}
    for k, tag in (("x", "X"), ("y", "Y"), ("z", "Z")):
        c = el.find(tag)
        if c is not None and c.text is not None:
            out[k] = float(c.text)
    return out or None


def _ftext(el: Optional[ET.Element], tag: str) -> Optional[float]:
    c = el.find(tag) if el is not None else None
    if c is not None and c.text is not None:
        try:
            return float(c.text)
        except ValueError:
            return None
    return None


def _stext(el: Optional[ET.Element], tag: str) -> Optional[str]:
    c = el.find(tag) if el is not None else None
    return c.text if c is not None and c.text is not None else None


def _serialize(root: ET.Element) -> bytes:
    ET.indent(root)  # py3.9+
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode").encode("utf-8")


# --------------------------------------------------------------------------- #
# Viewpoint  (BCF-API JSON  <->  .bcfv VisualizationInfo XML)
# --------------------------------------------------------------------------- #

def viewpoint_json_to_bcfv(viewpoint: dict, guid: str) -> bytes:
    """Serialize a BCF-API viewpoint JSON to a ``.bcfv`` (VisualizationInfo) XML."""
    root = ET.Element("VisualizationInfo", {"Guid": guid})

    comps = viewpoint.get("components") or {}
    selection = comps.get("selection") or []
    visibility = comps.get("visibility") or {}
    if selection or visibility:
        c = ET.SubElement(root, "Components")
        if selection:
            sel = ET.SubElement(c, "Selection")
            for comp in selection:
                _component(sel, comp)
        if visibility:
            vis = ET.SubElement(c, "Visibility",
                                {"DefaultVisibility": _bool(visibility.get("default_visibility", True))})
            exceptions = visibility.get("exceptions") or []
            if exceptions:
                ex = ET.SubElement(vis, "Exceptions")
                for comp in exceptions:
                    _component(ex, comp)

    pc = viewpoint.get("perspective_camera")
    if isinstance(pc, dict):
        el = ET.SubElement(root, "PerspectiveCamera")
        _vec3(el, "CameraViewPoint", pc.get("camera_view_point"))
        _vec3(el, "CameraDirection", pc.get("camera_direction"))
        _vec3(el, "CameraUpVector", pc.get("camera_up_vector"))
        _text(el, "FieldOfView", pc.get("field_of_view"))

    oc = viewpoint.get("orthogonal_camera")
    if isinstance(oc, dict):
        el = ET.SubElement(root, "OrthogonalCamera")
        _vec3(el, "CameraViewPoint", oc.get("camera_view_point"))
        _vec3(el, "CameraDirection", oc.get("camera_direction"))
        _vec3(el, "CameraUpVector", oc.get("camera_up_vector"))
        _text(el, "ViewToWorldScale", oc.get("view_to_world_scale"))

    planes = viewpoint.get("clipping_planes") or []
    if planes:
        cps = ET.SubElement(root, "ClippingPlanes")
        for p in planes:
            if not isinstance(p, dict):
                continue
            cp = ET.SubElement(cps, "ClippingPlane")
            _vec3(cp, "Location", p.get("location"))
            _vec3(cp, "Direction", p.get("direction"))

    return _serialize(root)


def _component(parent: ET.Element, comp: dict) -> None:
    """A ``<Component IfcGuid=.. AuthoringToolId=..>``. The IfcGuid is the native
    22-char compressed GlobalId; a non-IFC ref may only have an authoring-tool id."""
    if not isinstance(comp, dict):
        return
    attrs = {}
    ifc = comp.get("ifc_guid")
    if ifc:
        attrs["IfcGuid"] = str(ifc)
    tool_id = comp.get("authoring_tool_id") or comp.get("id")
    el = ET.SubElement(parent, "Component", attrs)
    if tool_id:
        _text(el, "AuthoringToolId", tool_id)


def _bool(v) -> str:
    return "true" if v else "false"


def bcfv_to_viewpoint_json(xml: bytes) -> dict:
    """Parse a ``.bcfv`` VisualizationInfo XML back to BCF-API viewpoint JSON."""
    root = ET.fromstring(xml)
    vp: dict = {}

    comps_el = root.find("Components")
    if comps_el is not None:
        components: dict = {}
        sel_el = comps_el.find("Selection")
        if sel_el is not None:
            sel = [_read_component(c) for c in sel_el.findall("Component")]
            if sel:
                components["selection"] = sel
        vis_el = comps_el.find("Visibility")
        if vis_el is not None:
            vis = {"default_visibility": vis_el.get("DefaultVisibility", "true") == "true"}
            ex_el = vis_el.find("Exceptions")
            if ex_el is not None:
                ex = [_read_component(c) for c in ex_el.findall("Component")]
                if ex:
                    vis["exceptions"] = ex
            components["visibility"] = vis
        if components:
            vp["components"] = components

    pc_el = root.find("PerspectiveCamera")
    if pc_el is not None:
        vp["perspective_camera"] = {
            "camera_view_point": _read_vec3(pc_el.find("CameraViewPoint")),
            "camera_direction": _read_vec3(pc_el.find("CameraDirection")),
            "camera_up_vector": _read_vec3(pc_el.find("CameraUpVector")),
            "field_of_view": _ftext(pc_el, "FieldOfView"),
        }
    oc_el = root.find("OrthogonalCamera")
    if oc_el is not None:
        vp["orthogonal_camera"] = {
            "camera_view_point": _read_vec3(oc_el.find("CameraViewPoint")),
            "camera_direction": _read_vec3(oc_el.find("CameraDirection")),
            "camera_up_vector": _read_vec3(oc_el.find("CameraUpVector")),
            "view_to_world_scale": _ftext(oc_el, "ViewToWorldScale"),
        }

    cps_el = root.find("ClippingPlanes")
    if cps_el is not None:
        planes = []
        for cp in cps_el.findall("ClippingPlane"):
            planes.append({"location": _read_vec3(cp.find("Location")),
                           "direction": _read_vec3(cp.find("Direction"))})
        if planes:
            vp["clipping_planes"] = planes
    return vp


def _read_component(el: ET.Element) -> dict:
    out: dict = {}
    if el.get("IfcGuid"):
        out["ifc_guid"] = el.get("IfcGuid")
    tool = _stext(el, "AuthoringToolId")
    if tool:
        out["authoring_tool_id"] = tool
    return out


# --------------------------------------------------------------------------- #
# Markup  (Topic + Comments + Viewpoint refs  <->  markup.bcf XML)
# --------------------------------------------------------------------------- #

def topic_to_markup(topic: dict) -> bytes:
    """Serialize a topic (+ its flat comments and viewpoint refs) to markup.bcf."""
    root = ET.Element("Markup")

    t = ET.SubElement(root, "Topic", {"Guid": topic["guid"],
                                      "TopicType": topic.get("topic_type", "Issue"),
                                      "TopicStatus": topic.get("topic_status", "Open")})
    _text(t, "Title", topic.get("title", ""))
    _text(t, "Priority", topic.get("priority"))
    _text(t, "CreationDate", topic.get("creation_date"))
    _text(t, "CreationAuthor", topic.get("creation_author"))

    # Flat comment list (BCF has no nested replies — §16 flatten on export).
    for c in topic.get("comments") or []:
        ce = ET.SubElement(root, "Comment", {"Guid": c["guid"]})
        _text(ce, "Date", c.get("date"))
        _text(ce, "Author", c.get("author"))
        _text(ce, "Comment", c.get("comment", ""))
        if c.get("viewpoint_guid"):
            ET.SubElement(ce, "Viewpoint", {"Guid": c["viewpoint_guid"]})

    # Viewpoint index: each references its .bcfv + snapshot.png by conventional name.
    for v in topic.get("viewpoints") or []:
        vg = v["guid"]
        ve = ET.SubElement(root, "Viewpoints", {"Guid": vg})
        _text(ve, "Viewpoint", f"{vg}.bcfv")
        if v.get("snapshot"):
            _text(ve, "Snapshot", f"{vg}.png")
    return _serialize(root)


def markup_to_topic(xml: bytes) -> dict:
    """Parse markup.bcf into a topic dict (viewpoints carry refs, not the .bcfv yet
    — ``import_bcfzip`` resolves those from the sibling files)."""
    root = ET.fromstring(xml)
    t = root.find("Topic")
    topic: dict = {
        "guid": t.get("Guid") if t is not None else None,
        "topic_type": t.get("TopicType") if t is not None else None,
        "topic_status": t.get("TopicStatus") if t is not None else None,
        "title": _stext(t, "Title"),
        "priority": _stext(t, "Priority"),
        "creation_date": _stext(t, "CreationDate"),
        "creation_author": _stext(t, "CreationAuthor"),
        "comments": [],
        "viewpoints": [],
    }
    for ce in root.findall("Comment"):
        vp = ce.find("Viewpoint")
        topic["comments"].append({
            "guid": ce.get("Guid"),
            "date": _stext(ce, "Date"),
            "author": _stext(ce, "Author"),
            "comment": _stext(ce, "Comment") or "",
            "viewpoint_guid": vp.get("Guid") if vp is not None else None,
        })
    for ve in root.findall("Viewpoints"):
        topic["viewpoints"].append({
            "guid": ve.get("Guid"),
            "viewpoint_file": _stext(ve, "Viewpoint"),
            "snapshot_file": _stext(ve, "Snapshot"),
        })
    return topic


# --------------------------------------------------------------------------- #
# .bcfzip package  (import / export)
# --------------------------------------------------------------------------- #

def export_bcfzip(topics: List[dict], *, version: str = BCF_VERSION) -> bytes:
    """Package an issue set into a BCF ``.bcfzip``: a ``bcf.version`` plus one
    ``{topic-guid}/`` folder each with ``markup.bcf``, a ``.bcfv`` per viewpoint,
    and ``.png`` snapshots. Topic/comment/viewpoint GUIDs are preserved for
    round-trip identity."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ver = ET.Element("Version", {"VersionId": version})
        _text(ver, "DetailedVersion", version)
        z.writestr("bcf.version", _serialize(ver))
        for topic in topics:
            tg = topic["guid"]
            z.writestr(f"{tg}/markup.bcf", topic_to_markup(topic))
            for v in topic.get("viewpoints") or []:
                vg = v["guid"]
                z.writestr(f"{tg}/{vg}.bcfv", viewpoint_json_to_bcfv(v.get("viewpoint") or {}, vg))
                if v.get("snapshot"):
                    z.writestr(f"{tg}/{vg}.png", v["snapshot"])
    return buf.getvalue()


def import_bcfzip(data: bytes) -> List[dict]:
    """Unzip a ``.bcfzip`` and parse each topic folder into an internal issue dict,
    resolving each viewpoint's ``.bcfv`` (→ viewpoint JSON) and ``.png`` snapshot.
    Upsert-by-guid is the caller's job (§11) — this only decodes."""
    topics: List[dict] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        markups = sorted(n for n in names if n.endswith("/markup.bcf"))
        for markup_name in markups:
            folder = markup_name[: -len("markup.bcf")]  # "<guid>/"
            topic = markup_to_topic(z.read(markup_name))
            resolved = []
            for v in topic.get("viewpoints") or []:
                vg = v.get("guid")
                vp_json = {}
                vf = v.get("viewpoint_file") or (f"{vg}.bcfv" if vg else None)
                if vf and (folder + vf) in names:
                    vp_json = bcfv_to_viewpoint_json(z.read(folder + vf))
                snapshot = None
                sf = v.get("snapshot_file")
                if sf and (folder + sf) in names:
                    snapshot = z.read(folder + sf)
                resolved.append({"guid": vg, "viewpoint": vp_json, "snapshot": snapshot})
            topic["viewpoints"] = resolved
            topics.append(topic)
    return topics
