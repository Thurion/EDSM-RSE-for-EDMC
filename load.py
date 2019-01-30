"""
EDSM-RSE a plugin for EDMC
Copyright (C) 2017 Sebastian Bauer

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""

import sys
import time
import urllib2
from Queue import Queue

import Tkinter as tk
import ttk
from ttkHyperlinkLabel import HyperlinkLabel
import myNotebook as nb

from l10n import Locale
from config import config

from Backgroundworker import BackgroundWorker
from RseData import RseData
from BackgroundTask import JumpedSystemTask, NavbeaconTask, IgnoreSystemTask, VersionCheckTask, DeleteSystemsFromCacheTask

if __debug__:
    from traceback import print_exc

this = sys.modules[__name__]  # For holding module globals

this.rseData = None  # holding module wide variables
this.systemCreated = True  # initialize with true in case someone uses an older EDMC version that does not call edsm_notify_system()
this.enabled = False  # plugin configured correctly and therefore enabled

this.worker = None  # background worker
this.queue = None  # queue used by the background worker

this.clipboard = None  # (tk.IntVar) copy system name to clipboard
this.overwrite = None  # (tk.IntVar) overwrite disabled state (EDSM/EDDN disabled)
this.edsmBodyCheck = None  # (tk.IntVar) in settings; compare total number of bodies to the number known to EDSM

this.errorLabel = None  # (tk.Label) show if plugin can't work (EDSM/EDDN disabled)
this.distanceValue = None  # (tk.Label) distance to system
this.actionText = None  # (tk.Label) task to do
this.edsmBodyCountDescription = None  # (tk.Label) description of information about bodies known to EDSM
this.edsmBodyCountText = None  # (tk.Label) text of information about bodies known to EDSM

this.unconfirmedSystem = None  # (RseHyperlinkLabel) display name of system that needs checking


class RseHyperlinkLabel(HyperlinkLabel):

    def __init__(self, master=None, **kw):
        super(RseHyperlinkLabel, self).__init__(master, **kw)
        self.menu.add_command(label=_("Ignore this session"), command=self.ignoreTemporarily)
        self.menu.add_command(label=_("Ignore for 24 hours"), command=self.ignoreFor24)
        self.menu.add_command(label=_("Ignore indefinitely"), command=self.ignoreIndefinitely)

    def ignoreTemporarily(self):
        this.queue.put(IgnoreSystemTask(this.rseData, self["text"]))

    def ignoreFor24(self):
        this.queue.put(IgnoreSystemTask(this.rseData, self["text"], time.time() + 24 * 3600))

    def ignoreIndefinitely(self):
        this.queue.put(IgnoreSystemTask(this.rseData, self["text"], sys.maxint))


def checkTransmissionOptions():
    eddn = (config.getint("output") & config.OUT_SYS_EDDN) == config.OUT_SYS_EDDN
    edsm = config.getint("edsm_out") and 1
    return eddn or edsm


def plugin_start(plugin_dir):
    this.rseData = RseData(plugin_dir)
    settings = config.getint("EDSM-RSE") or 5  # default setting: radius 0 is currently not selectable
    this.clipboard = tk.IntVar(value=((settings >> 5) & 0x01))
    this.overwrite = tk.IntVar(value=((settings >> 6) & 0x01))
    this.edsmBodyCheck = tk.IntVar(value=((settings >> 7) & 0x01))

    this.enabled = checkTransmissionOptions()

    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue, this.rseData)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.radiusExponent = RseData.DEFAULT_RADIUS_EXPONENT
    this.worker.start()

    return "EDSM-RSE"


def updateUI(event=None):
    eliteSystem = this.rseData.lastEventInfo.get(RseData.BG_SYSTEM, None)
    message = this.rseData.lastEventInfo.get(RseData.BG_MESSAGE, None)
    if (this.enabled or this.overwrite.get()) and eliteSystem:
        this.errorLabel.grid_remove()
        this.unconfirmedSystem.grid(row=0, column=1, sticky=tk.W)
        this.unconfirmedSystem["text"] = eliteSystem.name
        this.unconfirmedSystem["url"] = "https://www.edsm.net/show-system?systemName={}".format(urllib2.quote(eliteSystem.name))
        this.unconfirmedSystem["state"] = "enabled"
        distanceText = u"{distance} Ly".format(distance=Locale.stringFromNumber(eliteSystem.distance, 2))
        if eliteSystem.uncertainty > 0:
            distanceText = distanceText + u" (\u00B1{uncertainty})".format(uncertainty=eliteSystem.uncertainty)
        this.distanceValue["text"] = distanceText
        this.actionText["text"] = eliteSystem.action_text
        if this.clipboard.get():
            this.frame.clipboard_clear()
            this.frame.clipboard_append(eliteSystem.name)
    else:
        this.unconfirmedSystem.grid_remove()
        this.errorLabel.grid(row=0, column=1, sticky=tk.W)
        this.distanceValue["text"] = "?"
        this.actionText["text"] = "?"
        if not this.enabled and not this.overwrite.get():
            this.errorLabel["text"] = "EDSM/EDDN is disabled"
        else:
            this.errorLabel["text"] = message or "?"

    if this.edsmBodyCheck.get():
        this.edsmBodyCountDescription.grid(row=3, column=0, sticky=tk.W)
        this.edsmBodyCountText.grid(row=3, column=1, sticky=tk.W)
    else:
        this.edsmBodyCountDescription.grid_remove()
        this.edsmBodyCountText.grid_remove()


def plugin_close():
    # Signal thread to close and wait for it
    this.queue.put(None)
    this.worker.join()
    this.worker = None


def edsmClearCacheCallback():
    this.queue.put(DeleteSystemsFromCacheTask(this.rseData, RseData.CACHE_FULLY_SCANNED_BODIES))


def plugin_prefs(parent):
    PADX = 5
    global row
    row = 0

    def nextRow():
        global row
        row += 1
        return row

    frame = nb.Frame(parent)
    frame.columnconfigure(0, weight=1)

    row = 0

    nb.Checkbutton(frame, variable=this.edsmBodyCheck,
                   text="Check if body information on EDSM is incomplete").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Button(frame, text="Clear cache of scanned systems", command=edsmClearCacheCallback).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=nextRow(), columnspan=2, padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Checkbutton(frame, variable=this.clipboard,
                   text="Copy system name to clipboard after jump").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Checkbutton(frame, variable=this.overwrite,
                   text="I use another tool to transmit data to EDSM/EDDN").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)

    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=nextRow(), columnspan=2, padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Plugin Version: {}".format(RseData.VERSION)).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="Open the Github page for this plugin", background=nb.Label().cget("background"),
                   url="https://github.com/Thurion/EDSM-RSE-for-EDMC", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="A big thanks to EDTS for providing the coordinates.", background=nb.Label().cget("background"),
                   url="http://edts.thargoid.space/", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    return frame


def prefs_changed():
    # bits are as follows:
    # 0-3 radius # not used anymore
    # 4-5 interval, not used anymore
    # 6: copy to clipboard
    # 7: overwrite enabled status
    # 8: EDSM body check
    settings = (this.clipboard.get() << 5) | (this.overwrite.get() << 6) | (this.edsmBodyCheck.get() << 7)
    config.set("EDSM-RSE", settings)
    this.enabled = checkTransmissionOptions()
    this.rseData.radiusExponent = RseData.DEFAULT_RADIUS_EXPONENT

    updateUI()


def showUpdateNotification(event=None):
    updateVersionInfo = this.rseData.lastEventInfo.get(RseData.BG_JSON, None)
    if updateVersionInfo:
        url = updateVersionInfo["url"]
        text = "Plugin update to {version} available".format(version=updateVersionInfo["version"])
    else:
        url = "https://github.com/Thurion/EDSM-RSE-for-EDMC/releases"
        text = "Plugin update available"
    this.updateNotificationLabel["url"] = url
    this.updateNotificationLabel["text"] = text
    if this.edsmBodyCheck.get():
        this.updateNotificationLabel.grid(column=0, columnspan=2, sticky=tk.W)
    else:
        this.updateNotificationLabel.grid(column=0, columnspan=2, sticky=tk.W)


def plugin_app(parent):
    this.frame = tk.Frame(parent)
    this.frame.bind_all(RseData.EVENT_RSE_BACKGROUNDWORKER, updateUI)
    this.frame.bind_all(RseData.EVENT_RSE_UPDATE_AVAILABLE, showUpdateNotification)

    this.rseData.setFrame(this.frame)

    this.frame.columnconfigure(1, weight=1)
    tk.Label(this.frame, text="Unconfirmed:").grid(row=0, column=0, sticky=tk.W)
    this.unconfirmedSystem = RseHyperlinkLabel(this.frame, compound=tk.RIGHT, popup_copy=True)
    this.errorLabel = tk.Label(this.frame)
    tk.Label(this.frame, text="Distance:").grid(row=1, column=0, sticky=tk.W)
    this.distanceValue = tk.Label(this.frame)
    this.distanceValue.grid(row=1, column=1, sticky=tk.W)
    tk.Label(this.frame, text="Action:").grid(row=2, column=0, sticky=tk.W)
    this.actionText = tk.Label(this.frame)
    this.actionText.grid(row=2, column=1, sticky=tk.W)

    this.edsmBodyCountDescription = tk.Label(this.frame, text="EDSM body count:")
    this.edsmBodyCountText = tk.Label(this.frame)
    this.edsmBodyCountText["text"] = "?"

    this.updateNotificationLabel = HyperlinkLabel(this.frame, text="Plugin update available", background=nb.Label().cget("background"),
                                                  url="https://github.com/Thurion/EDSM-RSE-for-EDMC/releases", underline=True)
    updateUI()

    # start update check after frame is initialized to avoid any possible race conditions when generating the event
    this.queue.put(VersionCheckTask(this.rseData))

    return this.frame


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not this.enabled and not this.overwrite.get() or is_beta:
        return  # nothing to do here
    if entry["event"] == "FSDJump" or entry["event"] == "Location":
        if "StarPos" in entry:
            this.queue.put(JumpedSystemTask(this.rseData, entry["StarPos"], entry["SystemAddress"]))
    if entry["event"] == "Resurrect":
        # reset radius in case someone died in an area where there are not many available stars (meaning very large radius)
        this.rseData.radius = RseData.DEFAULT_RADIUS_EXPONENT
    if entry["event"] == "NavBeaconScan":
        this.queue.put(NavbeaconTask(this.rseData, entry["SystemAddress"]))

    # TODO FSSDiscoveryScan, FSSAllBodiesFound


def edsm_notify_system(reply):
    if reply.get('systemCreated'):
        this.systemCreated = True
    else:
        this.systemCreated = False
