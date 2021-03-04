# -*- coding: utf-8 -*-
"""
FBX Exporter
"""

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import


import os
import json
import struct
from textwrap import dedent
from functools import partial
from collections import defaultdict, OrderedDict

import qrenderdoc as qrd
import renderdoc as rd
from typing import Optional

FBX_ASCII_TEMPLETE = """; FBX 7.3.0 project file
; ----------------------------------------------------

; Object definitions
;------------------------------------------------------------------

Definitions:  {
    ObjectType: "Geometry" {
        Count: 1
        PropertyTemplate: "FbxMesh" {
            Properties70:  {
                P: "Primary Visibility", "bool", "", "",1
            }
        }
    }
    ObjectType: "Model" {
        Count: 1
        PropertyTemplate: "FbxNode" {
            Properties70:  {
                P: "Visibility", "Visibility", "", "A",1
            }
        }
    }
}

; Object properties
;------------------------------------------------------------------

Objects:  {
    Geometry: 2035541511296, "Geometry::", "Mesh" {
        Vertices: *%(vertices_num)s {
            a: %(vertices)s
        } 
        PolygonVertexIndex: *%(polygons_num)s {
            a: %(polygons)s
        } 
        GeometryVersion: 124%(LayerElementNormal)s%(LayerElementTangent)s%(LayerElementColor)s%(LayerElementUV)s%(LayerElementUV1)s
        Layer: 0 {
            Version: 100%(LayerElementNormalInsert)s%(LayerElementTangentInsert)s%(LayerElementColorInsert)s%(LayerElementUVInsert)s
        }%(LayerElementUV1Insert)s
    }
    Model: 2035615390896, "Model::%(model_name)s", "Mesh" {
        Properties70:  {
            P: "DefaultAttributeIndex", "int", "Integer", "",0
        }
    }
}

; Object connections
;------------------------------------------------------------------

Connections:  {
    ;Model::pCube1, Model::RootNode
    C: "OO",2035615390896,0
    ;Geometry::, Model::pCube1
    C: "OO",2035541511296,2035615390896
}"""


class MeshData(rd.MeshFormat):
    indexOffset = 0
    name = ""


# Unpack a tuple of the given format, from the data
def unpackData(fmt, data):
    # We don't handle 'special' formats - typically bit-packed such as 10:10:10:2
    if fmt.Special():
        raise RuntimeError("Packed formats are not supported!")

    formatChars = {}
    #                                 012345678
    formatChars[rd.CompType.UInt] = "xBHxIxxxL"
    formatChars[rd.CompType.SInt] = "xbhxixxxl"
    formatChars[rd.CompType.Float] = "xxexfxxxd"  # only 2, 4 and 8 are valid

    # These types have identical decodes, but we might post-process them
    formatChars[rd.CompType.UNorm] = formatChars[rd.CompType.UInt]
    formatChars[rd.CompType.UScaled] = formatChars[rd.CompType.UInt]
    formatChars[rd.CompType.SNorm] = formatChars[rd.CompType.SInt]
    formatChars[rd.CompType.SScaled] = formatChars[rd.CompType.SInt]

    # We need to fetch compCount components
    vertexFormat = str(fmt.compCount) + formatChars[fmt.compType][fmt.compByteWidth]

    # Unpack the data
    value = struct.unpack_from(vertexFormat, data, 0)

    # If the format needs post-processing such as normalisation, do that now
    if fmt.compType == rd.CompType.UNorm:
        divisor = float((2 ** (fmt.compByteWidth * 8)) - 1)
        value = tuple(float(i) / divisor for i in value)
    elif fmt.compType == rd.CompType.SNorm:
        maxNeg = -float(2 ** (fmt.compByteWidth * 8)) / 2
        divisor = float(-(maxNeg - 1))
        value = tuple(
            (float(i) if (i == maxNeg) else (float(i) / divisor)) for i in value
        )

    # If the format is BGRA, swap the two components
    if fmt.BGRAOrder():
        value = tuple(value[i] for i in [2, 1, 0, 3])
        
    # keep four digits
    value = tuple(float("%.4f" % value[i]) for i in range(len(value)))

    return value


def getIndices(controller, mesh):
    # Get the character for the width of index
    indexFormat = "B"
    if mesh.indexByteStride == 2:
        indexFormat = "H"
    elif mesh.indexByteStride == 4:
        indexFormat = "I"

    # Duplicate the format by the number of indices
    indexFormat = str(mesh.numIndices) + indexFormat

    # If we have an index buffer
    if mesh.indexResourceId != rd.ResourceId.Null():
        # Fetch the data
        ibdata = controller.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset, 0)

        # Unpack all the indices, starting from the first index to fetch
        offset = mesh.indexOffset * mesh.indexByteStride
        indices = struct.unpack_from(indexFormat, ibdata, offset)

        # Apply the baseVertex offset
        return [i + mesh.baseVertex for i in indices]
    else:
        # With no index buffer, just generate a range
        return tuple(range(mesh.numIndices))

def change_triangle_orient(list):
    for i, v in enumerate(list):
        if i % 3 == 0:
            temp = list[i - 1]
            list[i - 1] = list[i - 2]
            list[i - 2] = temp

def export_fbx(save_path, meshInputs, controller):
    manager = pyrenderdoc.Extensions()

    indices = getIndices(controller, meshInputs[0])
    if not indices:
        # manager.ErrorDialog("Current Draw Call lack of Vertex. ", "Error")
        return

    save_name = os.path.basename(os.path.splitext(save_path)[0])

    idx_list = []
    value_dict = defaultdict(list)
    vertex_data = defaultdict(OrderedDict)
    idx2newIdx = defaultdict(list)
    newIdx = 0
    for i, idx in enumerate(indices):

        if idx not in idx2newIdx:
            idx2newIdx[idx] = newIdx
            newIdx = newIdx + 1

        idx_list.append(idx2newIdx[idx])

        for attr in meshInputs:

            if idx not in vertex_data[attr.name]:
                
                # This is the data we're reading from. This would be good to cache instead of
                # re-fetching for every attribute for every index
                offset = attr.vertexByteOffset + attr.vertexByteStride * idx
                data = controller.GetBufferData(attr.vertexResourceId, offset, attr.vertexByteStride)

                # Get the value from the data
                value = unpackData(attr.format, data)

                vertex_data[attr.name][idx] = value

            value_dict[attr.name].append(vertex_data[attr.name][idx])

    # change_triangle_orient(idx_list)

    idx_data = ",".join([str(v) for v in idx_list])
    idx_len = len(idx_list)

    ARGS = {"model_name": save_name}
    vertices = [str(v) for values in vertex_data["in_POSITION0"].values() for v in values]
    # vertices = [str(-v) if i == 0 else str(v) for values in vertex_data["in_POSITION0"].values() for i, v in enumerate(values)]
    ARGS["vertices"] = ",".join(vertices)
    ARGS["vertices_num"] = len(vertices)

    polygons = [str(v) if i % 3 else str(-(v + 1)) for i, v in enumerate(idx_list, 1)]
    ARGS["polygons"] = ",".join(polygons)
    ARGS["polygons_num"] = len(polygons)

    LayerElementNormal = ""
    LayerElementNormalInsert = ""
    has_normal = vertex_data.get("in_NORMAL0")

    if has_normal:
        normals = [str(values[v]) for values in value_dict["in_NORMAL0"] for v in [0,1,2]]

        LayerElementNormal = """
        LayerElementNormal: 0 {
            Version: 101
            Name: ""
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "Direct"
            Normals: *%(normals_num)s {
                a: %(normals)s
            }
        }""" % {
            "normals": ",".join(normals),
            "normals_num": len(normals),
        }
        LayerElementNormalInsert = """
            LayerElement:  {
                Type: "LayerElementNormal"
                TypedIndex: 0
            }"""

    LayerElementTangent = ""
    LayerElementTangentInsert = ""
    has_tangent = vertex_data.get("in_TANGENT0")
    if has_tangent:
        tangents = [str(v) for values in value_dict["in_TANGENT0"] for v in values]
        LayerElementTangent = """
        LayerElementTangent: 0 {
            Version: 101
            Name: ""
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "Direct"
            Tangents: *%(tangents_num)s {
                a: %(tangents)s
            } 
        }""" % {
            "tangents": ",".join(tangents),
            "tangents_num": len(tangents),
        }

        LayerElementTangentInsert = """
            LayerElement:  {
                Type: "LayerElementTangent"
                TypedIndex: 0
            }"""

    LayerElementColor = ""
    LayerElementColorInsert = ""
    has_color = vertex_data.get("in_COLOR0")
    if has_color:
        colors = [
            str(v) if i % 4 else "1"
            for values in value_dict["in_COLOR0"]
            for i, v in enumerate(values, 1)
        ]

        LayerElementColor = """
            LayerElementColor: 0 {
                Version: 101
                Name: "colorSet1"
                MappingInformationType: "ByPolygonVertex"
                ReferenceInformationType: "IndexToDirect"
                Colors: *%(colors_num)s {
                    a: %(colors)s
                } 
                ColorIndex: *%(colors_indices_num)s {
                    a: %(colors_indices)s
                } 
            }""" % {
            "colors": ",".join(colors),
            "colors_num": len(colors),
            "colors_indices": ",".join([str(i) for i in range(idx_len)]),
            "colors_indices_num": idx_len,
        }
        LayerElementColorInsert = """
            LayerElement:  {
                Type: "LayerElementColor"
                TypedIndex: 0
            }"""

    LayerElementUV = ""
    LayerElementUVInsert = ""
    has_uv = vertex_data.get("in_TEXCOORD0")
    if has_uv:
        uvs = [str(v) for values in vertex_data["in_TEXCOORD0"].values() for v in values]

        LayerElementUV = """
        LayerElementUV: 0 {
            Version: 101
            Name: ""
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "IndexToDirect"
            UV: *%(uvs_num)s {
                a: %(uvs)s
            } 
            UVIndex: *%(uvs_indices_num)s {
                a: %(uvs_indices)s
            } 
        }""" % {
            "uvs": ",".join(uvs),
            "uvs_num": len(uvs),
            "uvs_indices": idx_data,
            "uvs_indices_num": idx_len,
        }

        LayerElementUVInsert = """
            LayerElement:  {
                Type: "LayerElementUV"
                TypedIndex: 0
            }"""

    LayerElementUV1 = ""
    LayerElementUV1Insert = ""
    has_uv1 = vertex_data.get("in_TEXCOORD1")
    if has_uv1:
        uvs = [str(v) for values in vertex_data["in_TEXCOORD1"].values() for v in values]

        LayerElementUV1 = """
        LayerElementUV: 1 {
            Version: 101
            Name: ""
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "IndexToDirect"
            UV: *%(uvs_num)s {
                a: %(uvs)s
            } 
            UVIndex: *%(uvs_indices_num)s {
                a: %(uvs_indices)s
            } 
        }""" % {
            "uvs": ",".join(uvs),
            "uvs_num": len(uvs),
            "uvs_indices": idx_data,
            "uvs_indices_num": idx_len,
        }

        LayerElementUV1Insert = """
        Layer: 1 {
            Version: 100
            LayerElement:  {
                Type: "LayerElementUV"
                TypedIndex: 1
            }
        }"""

    ARGS.update(
        {
            "LayerElementNormal": LayerElementNormal,
            "LayerElementNormalInsert": LayerElementNormalInsert,
            "LayerElementTangent": LayerElementTangent,
            "LayerElementTangentInsert": LayerElementTangentInsert,
            "LayerElementColor": LayerElementColor,
            "LayerElementColorInsert": LayerElementColorInsert,
            "LayerElementUV": LayerElementUV,
            "LayerElementUVInsert": LayerElementUVInsert,
            "LayerElementUV1": LayerElementUV1,
            "LayerElementUV1Insert": LayerElementUV1Insert,
        }
    )

    fbx = FBX_ASCII_TEMPLETE % ARGS

    with open(save_path, "w") as f:
        f.write(dedent(fbx).strip())

class Exporter:

    def __init__(self, ctx: qrd.CaptureContext, eid: int, path: str, r: rd.ReplayController):
        self.ctx = ctx
        self.eid = eid
        self.path = path
        self.r = r

        self.result = None

        self.export_by_event()
        
    def export_by_event(self):
        draw = self.ctx.GetDrawcall(self.eid)
        if draw is None:
            self.result = "not a valid eventID"
            return

        self.r.SetFrameEvent(self.eid, False)
        state = self.r.GetPipelineState()

        # Get the index & vertex buffers, and fixed vertex inputs
        ib = state.GetIBuffer()
        vbs = state.GetVBuffers()
        attrs = state.GetVertexInputs()

        meshInputs = []
        for attr in attrs:
            if not attr.used:
                continue
            elif attr.perInstance:
                # We don't handle instance attributes
                self.result = "Instanced properties are not supported!"
                return

            meshInput = MeshData()
            meshInput.indexResourceId = ib.resourceId
            meshInput.indexByteOffset = ib.byteOffset
            meshInput.indexByteStride = draw.indexByteWidth
            meshInput.baseVertex = draw.baseVertex
            meshInput.indexOffset = draw.indexOffset
            meshInput.numIndices = draw.numIndices

            # If the draw doesn't use an index buffer, don't use it even if bound
            if not (draw.flags & rd.DrawFlags.Indexed):
                meshInput.indexResourceId = rd.ResourceId.Null()

            # The total offset is the attribute offset from the base of the vertex
            meshInput.vertexByteOffset = (
                attr.byteOffset
                + vbs[attr.vertexBuffer].byteOffset
                + draw.vertexOffset * vbs[attr.vertexBuffer].byteStride
            )
            meshInput.format = attr.format
            meshInput.vertexResourceId = vbs[attr.vertexBuffer].resourceId
            meshInput.vertexByteStride = vbs[attr.vertexBuffer].byteStride
            meshInput.name = attr.name

            meshInputs.append(meshInput)

        finalPath = self.path + "/drawcall_" + str(draw.drawcallId) + ".fbx"
        print(finalPath)
        export_fbx(finalPath, meshInputs, self.r)

    def get_result(self):
        return self.result

# async
def export_wrap(ctx: qrd.CaptureContext, eid: int, save_path: str, finished_callback):
    # define a local function that wraps the detail of needing to invoke back/forth onto replay thread
    def _replay_callback(r: rd.ReplayController):
        exporter = Exporter(ctx, eid, save_path, r)

        # Invoke back onto the UI thread to display the results
        ctx.Extensions().GetMiniQtHelper().InvokeOntoUIThread(lambda: finished_callback(exporter.get_result()))

    ctx.Replay().AsyncInvoke('fbx_exporter', _replay_callback)

# block
def export_wrap_block(ctx: qrd.CaptureContext, eid: int, save_path: str, finished_callback):

    def _replay_callback(r: rd.ReplayController):
        exporter = Exporter(ctx, eid, save_path, r)
        finished_callback(exporter.get_result())

    ctx.Replay().BlockInvoke(_replay_callback)
    
class Window(qrd.CaptureViewer):
    def __init__(self, ctx: qrd.CaptureContext, version: str):
        super().__init__()

        self.mqt: qrd.MiniQtHelper = ctx.Extensions().GetMiniQtHelper()

        self.save_path = None

        self.ctx = ctx
        self.version = version
        self.topWindow = self.mqt.CreateToplevelWidget("FBX Exporter", lambda c, w, d: window_closed())

        vert = self.mqt.CreateVerticalContainer()
        self.mqt.AddWidget(self.topWindow, vert)
        
        self.eventLabel = self.mqt.CreateLabel()
        self.mqt.SetWidgetText(self.eventLabel, "EventID:")
        self.eventTextBox = self.mqt.CreateTextBox(True, None)
        horiz = self.mqt.CreateHorizontalContainer()
        self.mqt.AddWidget(horiz, self.eventLabel)
        self.mqt.AddWidget(horiz, self.eventTextBox)
        self.mqt.AddWidget(vert, horiz)

        self.folderLabel = self.mqt.CreateLabel()
        self.folderButton = self.mqt.CreateButton(lambda c, w, d: self.select_folder())
        self.mqt.SetWidgetText(self.folderButton, "Select Folder")
        horiz = self.mqt.CreateHorizontalContainer()
        self.mqt.AddWidget(horiz, self.folderLabel)
        self.mqt.AddWidget(horiz, self.folderButton)
        self.mqt.AddWidget(vert, horiz)

        self.exportButton = self.mqt.CreateButton(lambda c, w, d: self.start_export())
        self.mqt.SetWidgetText(self.exportButton, "Export")
        self.mqt.AddWidget(vert, self.exportButton)
        
        self.refresh()

        ctx.AddCaptureViewer(self)

    def OnCaptureLoaded(self):
        pass

    def OnCaptureClosed(self):
        pass

    def OnSelectedEventChanged(self, event):
        pass

    def OnEventChanged(self, event):
        pass

    def select_folder(self):
        self.save_path = self.ctx.Extensions().OpenDirectoryName("Select Folder")
        self.refresh()

    def refresh(self):
        self.mqt.SetWidgetEnabled(self.exportButton, self.save_path is not None)
        self.mqt.SetWidgetText(self.folderLabel, "Folder:" + str(self.save_path))

    def start_export(self):
        try:
            eventID = int(self.mqt.GetWidgetText(self.eventTextBox))
        except:
            self.ctx.Extensions().MessageDialog("not a valid number", "Error")
            return

        if eventID < 0:
            self.ctx.Extensions().MessageDialog("not a valid eventId", "Error")
            return
            
        export_wrap(self.ctx, eventID, self.save_path, lambda results: self.finish_export(results))

    def finish_export(self, result):
        if result:
            self.ctx.Extensions().MessageDialog(result, "Failed")
        else:
            self.ctx.Extensions().MessageDialog("fbx saved", "Congradualtion!~")
            os.startfile(self.save_path)


cur_window: Optional[Window] = None
    
def window_closed():
    global cur_window

    if cur_window is not None:
        cur_window.ctx.RemoveCaptureViewer(cur_window)

    cur_window = None
    
def window_callback(ctx: qrd.CaptureContext, data):
    global cur_window

    if cur_window is None:
        cur_window = Window(ctx, extiface_version)
        if ctx.HasEventBrowser():
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.TopOf, ctx.GetEventBrowser().Widget(), 0.1)
        else:
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.MainToolArea, None)

    ctx.RaiseDockWindow(cur_window.topWindow)

extiface_version = ''

def register(version: str, ctx: qrd.CaptureContext):
    global extiface_version
    extiface_version = version

    # version is the RenderDoc Major.Minor version as a string, such as "1.2"
    # pyrenderdoc is the CaptureContext handle, the same as the global available in the python shell
    print("Registering FBX Mesh Exporter extension for RenderDoc {}".format(version))

    ctx.Extensions().RegisterPanelMenu(qrd.PanelMenu.MeshPreview, ["Export FBX Mesh"], prepare_export)
    ctx.Extensions().RegisterWindowMenu(qrd.WindowMenu.Window, ["FBX Exporter"], window_callback)


def unregister():
    print("Unregistrating FBX Mesh Exporter extension")

    global cur_window

    if cur_window is not None:
        # The window_closed() callback will unregister the capture viewer
        cur_window.ctx.Extensions().GetMiniQtHelper().CloseToplevelWidget(cur_window.topWindow)
        cur_window = None
