###############################################################################
# The MIT License (MIT)
#
# Copyright (c) 2021 timmyliang
# Copyright (c) 2021 ericchan
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
###############################################################################

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
        self.export_fbx(finalPath, meshInputs)

    def export_fbx(self, save_path, meshInputs):
        indices = getIndices(self.r, meshInputs[0])
        if not indices:
            self.result = "Current Draw Call lack of Vertex"
            return

        save_name = os.path.basename(os.path.splitext(save_path)[0])

        idx_list = []
        self.value_dict = defaultdict(list)
        self.vertex_data = defaultdict(OrderedDict)
        idx2newIdx = defaultdict(list)
        newIdx = 0
        for i, idx in enumerate(indices):

            if idx not in idx2newIdx:
                idx2newIdx[idx] = newIdx
                newIdx = newIdx + 1

            idx_list.append(idx2newIdx[idx])

            for attr in meshInputs:

                if idx not in self.vertex_data[attr.name]:
                    
                    # This is the data we're reading from. This would be good to cache instead of
                    # re-fetching for every attribute for every index
                    offset = attr.vertexByteOffset + attr.vertexByteStride * idx
                    data = self.r.GetBufferData(attr.vertexResourceId, offset, attr.vertexByteStride)

                    # Get the value from the data
                    value = unpackData(attr.format, data)

                    self.vertex_data[attr.name][idx] = value

                self.value_dict[attr.name].append(self.vertex_data[attr.name][idx])

        # change_triangle_orient(idx_list)

        self.idx_data = ",".join([str(v) for v in idx_list])
        self.idx_len = len(idx_list)

        ARGS = {"model_name": save_name}
        vertices = [str(v) for values in self.vertex_data["in_POSITION0"].values() for v in values]
        # vertices = [str(-v) if i == 0 else str(v) for values in self.vertex_data["in_POSITION0"].values() for i, v in enumerate(values)]
        ARGS["vertices"] = ",".join(vertices)
        ARGS["vertices_num"] = len(vertices)

        polygons = [str(v) if i % 3 else str(-(v + 1)) for i, v in enumerate(idx_list, 1)]
        ARGS["polygons"] = ",".join(polygons)
        ARGS["polygons_num"] = len(polygons)

        self.build_normal()
        self.build_tangent()
        self.build_color()
        self.build_uv0()
        self.build_uv1()

        ARGS.update(
            {
                "LayerElementNormal": self.LayerElementNormal,
                "LayerElementNormalInsert": self.LayerElementNormalInsert,
                "LayerElementTangent": self.LayerElementTangent,
                "LayerElementTangentInsert": self.LayerElementTangentInsert,
                "LayerElementColor": self.LayerElementColor,
                "LayerElementColorInsert": self.LayerElementColorInsert,
                "LayerElementUV": self.LayerElementUV,
                "LayerElementUVInsert": self.LayerElementUVInsert,
                "LayerElementUV1": self.LayerElementUV1,
                "LayerElementUV1Insert": self.LayerElementUV1Insert,
            }
        )

        fbx = FBX_ASCII_TEMPLETE % ARGS

        with open(save_path, "w") as f:
            f.write(dedent(fbx).strip())

    def build_normal(self):
        self.LayerElementNormal = ""
        self.LayerElementNormalInsert = ""
        has_normal = self.vertex_data.get("in_NORMAL0")
        if has_normal:
            normals = [str(values[v]) for values in self.value_dict["in_NORMAL0"] for v in [0,1,2]]

            self.LayerElementNormal = """
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
            self.LayerElementNormalInsert = """
                LayerElement:  {
                    Type: "LayerElementNormal"
                    TypedIndex: 0
                }"""
    
    def build_tangent(self):
        self.LayerElementTangent = ""
        self.LayerElementTangentInsert = ""
        has_tangent = self.vertex_data.get("in_TANGENT0")
        if has_tangent:
            tangents = [str(v) for values in self.value_dict["in_TANGENT0"] for v in values]
            self.LayerElementTangent = """
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

            self.LayerElementTangentInsert = """
                LayerElement:  {
                    Type: "LayerElementTangent"
                    TypedIndex: 0
                }"""

    def build_color(self):
        self.LayerElementColor = ""
        self.LayerElementColorInsert = ""
        has_color = self.vertex_data.get("in_COLOR0")
        if has_color:
            colors = [
                str(v) if i % 4 else "1"
                for values in self.value_dict["in_COLOR0"]
                for i, v in enumerate(values, 1)
            ]

            self.LayerElementColor = """
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
                "colors_indices": ",".join([str(i) for i in range(self.idx_len)]),
                "colors_indices_num": self.idx_len,
            }
            self.LayerElementColorInsert = """
                LayerElement:  {
                    Type: "LayerElementColor"
                    TypedIndex: 0
                }"""

    def build_uv0(self):
        self.LayerElementUV = ""
        self.LayerElementUVInsert = ""
        has_uv = self.vertex_data.get("in_TEXCOORD0")
        if has_uv:
            uvs = [str(v) for values in self.vertex_data["in_TEXCOORD0"].values() for v in values]

            self.LayerElementUV = """
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
                "uvs_indices": self.idx_data,
                "uvs_indices_num": self.idx_len,
            }

            self.LayerElementUVInsert = """
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 0
                }"""
    
    def build_uv1(self):
        self.LayerElementUV1 = ""
        self.LayerElementUV1Insert = ""
        has_uv1 = self.vertex_data.get("in_TEXCOORD1")
        if has_uv1:
            uvs = [str(v) for values in self.vertex_data["in_TEXCOORD1"].values() for v in values]

            self.LayerElementUV1 = """
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
                "uvs_indices": self.idx_data,
                "uvs_indices_num": self.idx_len,
            }

            self.LayerElementUV1Insert = """
            Layer: 1 {
                Version: 100
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 1
                }
            }"""

    def get_result(self):
        return self.result

def export_wrap(ctx: qrd.CaptureContext, eid: int, save_path: str, finished_callback):
    # define a local function that wraps the detail of needing to invoke back/forth onto replay thread
    def _replay_callback(r: rd.ReplayController):
        exporter = Exporter(ctx, eid, save_path, r)

        # Invoke back onto the UI thread to display the results
        ctx.Extensions().GetMiniQtHelper().InvokeOntoUIThread(lambda: finished_callback(exporter.get_result()))

    ctx.Replay().AsyncInvoke('fbx_exporter', _replay_callback)