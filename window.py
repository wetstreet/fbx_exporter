###############################################################################
# The MIT License (MIT)
#
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

import os
import qrenderdoc as qrd
import renderdoc as rd
from typing import Optional
from . import exporter


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
        
        startDrawcallLabel = self.mqt.CreateLabel()
        self.mqt.SetWidgetText(startDrawcallLabel, "Start DrawCall ID:")
        self.startDrawcallTextBox = self.mqt.CreateTextBox(True, None)
        endDrawcallLabel = self.mqt.CreateLabel()
        self.mqt.SetWidgetText(endDrawcallLabel, "End DrawCall ID:")
        self.endDrawcallTextBox = self.mqt.CreateTextBox(True, None)
        horiz = self.mqt.CreateHorizontalContainer()
        self.mqt.AddWidget(horiz, startDrawcallLabel)
        self.mqt.AddWidget(horiz, self.startDrawcallTextBox)
        self.mqt.AddWidget(horiz, endDrawcallLabel)
        self.mqt.AddWidget(horiz, self.endDrawcallTextBox)
        self.mqt.AddWidget(vert, horiz)

        saveTextureLabel = self.mqt.CreateLabel()
        self.mqt.SetWidgetText(saveTextureLabel, "Save Texture:")
        self.saveTextureCheckBox = self.mqt.CreateCheckbox(None)
        horiz = self.mqt.CreateHorizontalContainer()
        self.mqt.AddWidget(horiz, saveTextureLabel)
        self.mqt.AddWidget(horiz, self.mqt.CreateSpacer(True))
        self.mqt.AddWidget(horiz, self.saveTextureCheckBox)
        self.mqt.AddWidget(vert, horiz)

        self.folderLabel = self.mqt.CreateLabel()
        folderButton = self.mqt.CreateButton(lambda c, w, d: self.select_folder())
        self.mqt.SetWidgetText(folderButton, "Select Folder")
        horiz = self.mqt.CreateHorizontalContainer()
        self.mqt.AddWidget(horiz, self.folderLabel)
        self.mqt.AddWidget(horiz, self.mqt.CreateSpacer(True))
        self.mqt.AddWidget(horiz, folderButton)
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
        self.mqt.SetWidgetText(self.folderLabel, "Destination Folder:" + str(self.save_path))

    def start_export(self):
        try:
            startDrawcallId = int(self.mqt.GetWidgetText(self.startDrawcallTextBox))
            endDrawcallId = int(self.mqt.GetWidgetText(self.endDrawcallTextBox))
        except:
            self.ctx.Extensions().MessageDialog("not a valid number", "Error")
            return

        if startDrawcallId < 0 or endDrawcallId < 0:
            self.ctx.Extensions().MessageDialog("not a valid drawcall id", "Error")
            return
            
        is_save_texture = self.mqt.IsWidgetChecked(self.saveTextureCheckBox)
        exporter.export_wrap(self.ctx, startDrawcallId, endDrawcallId, is_save_texture, self.save_path, lambda results: self.finish_export(results))

    def finish_export(self, result):
        if result:
            self.ctx.Extensions().MessageDialog(result, "Failed")
        else:
            self.ctx.Extensions().MessageDialog("Export Finished", "Congradualtion!~")
            os.startfile(self.save_path)


cur_window: Optional[Window] = None


def window_closed():
    global cur_window

    if cur_window is not None:
        cur_window.ctx.Extensions().GetMiniQtHelper().CloseToplevelWidget(cur_window.topWindow)
        cur_window.ctx.RemoveCaptureViewer(cur_window)

    cur_window = None


def close_window():
    global cur_window
    
    if cur_window is not None:
        cur_window.ctx.Extensions().GetMiniQtHelper().CloseToplevelWidget(cur_window.topWindow)
        cur_window = None


def get_window(ctx, version):
    global cur_window

    if cur_window is None:
        cur_window = Window(ctx, version)
        if ctx.HasEventBrowser():
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.TopOf, ctx.GetEventBrowser().Widget(), 0.1)
        else:
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.MainToolArea, None)

    return cur_window.topWindow