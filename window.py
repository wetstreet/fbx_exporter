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

def get_window(ctx, version):
    global cur_window

    if cur_window is None:
        cur_window = Window(ctx, version)
        if ctx.HasEventBrowser():
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.TopOf, ctx.GetEventBrowser().Widget(), 0.1)
        else:
            ctx.AddDockWindow(cur_window.topWindow, qrd.DockReference.MainToolArea, None)

    return cur_window.topWindow