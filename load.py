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
import urllib2
import thread
import json
from Queue import Queue

import Tkinter as tk
import ttk
from ttkHyperlinkLabel import HyperlinkLabel
import myNotebook as nb

from l10n import Locale
from config import config

from Backgroundworker import BackgroundWorker

if __debug__:
    from traceback import print_exc

this = sys.modules[__name__]  # For holding module globals

this.VERSION = "1.1"
this.VERSION_CHECK_URL = "https://gist.githubusercontent.com/Thurion/35553c9562297162a86722a28c7565ab/raw/update"
this.newVersionInfo = None

this.LAST_EVENT_INFO = dict()  # use only to read values. use clear() to clear but don't assign a new value to this variable!


class RseHyperlinkLabel(HyperlinkLabel):

    def __init__(self, master=None, **kw):
        super(RseHyperlinkLabel, self).__init__(master, **kw)
        self.menu.add_command(label=_("Ignore"), command=self.ignore)

    def ignore(self):
        this.worker.ignore(self["text"])


def checkTransmissionOptions():
    eddn = (config.getint("output") & config.OUT_SYS_EDDN) == config.OUT_SYS_EDDN
    edsm = config.getint("edsm_out") and 1
    return eddn or edsm


def plugin_start():
    settings = config.getint("EDSM-RSE") or 5  # default setting: radius 0 is currently not selectable
    this.clipboard = tk.IntVar(value=((settings >> 5) & 0x01))
    this.overwrite = tk.IntVar(value=((settings >> 6) & 0x01))

    this.enabled = checkTransmissionOptions()

    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue, this.LAST_EVENT_INFO)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.radius = BackgroundWorker.DEFAULT_RADIUS
    this.worker.start()

    return "EDSM-RSE"


def updateUI(event=None):
    eliteSystem = this.LAST_EVENT_INFO.get(BackgroundWorker.BG_SYSTEM, None)
    message = this.LAST_EVENT_INFO.get(BackgroundWorker.BG_MESSAGE, None)
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


def plugin_close():
    # Signal thread to close and wait for it
    this.queue.put((None, None))
    this.worker.join()
    this.worker = None


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
    nb.Checkbutton(frame, variable=this.clipboard,
                   text="Copy system name to clipboard after jump").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Checkbutton(frame, variable=this.overwrite,
                   text="I use another tool to transmit data to EDSM/EDDN").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)

    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=nextRow(), columnspan=2, padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Plugin Version: {}".format(this.VERSION)).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
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
    settings = (this.clipboard.get() << 5) | (this.overwrite.get() << 6)
    config.set("EDSM-RSE", settings)
    this.enabled = checkTransmissionOptions()
    this.worker.radius = BackgroundWorker.DEFAULT_RADIUS

    updateUI()


def versionCheck():
    try:
        request = urllib2.Request(this.VERSION_CHECK_URL)
        response = urllib2.urlopen(request)
        this.newVersionInfo = json.loads(response.read())
        if this.VERSION != this.newVersionInfo["version"]:
            this.frame.event_generate("<<EDSM-RSE_UpdateAvailable>>", when="tail")
    except ValueError:
        pass  # ignore


def showUpdateNotification(event=None):
    this.updateNotificationLabel["url"] = this.newVersionInfo["url"]
    this.updateNotificationLabel["text"] = "Plugin update to {version} available".format(version=this.newVersionInfo["version"])
    this.updateNotificationLabel.grid(row=3, column=0, columnspan=2, sticky=tk.W)


def plugin_app(parent):
    this.frame = tk.Frame(parent)
    this.frame.bind_all("<<EDSM-RSE_BackgroundWorker>>", updateUI)
    this.frame.bind_all("<<EDSM-RSE_UpdateAvailable>>", showUpdateNotification)

    this.worker.setFrame(this.frame)

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

    this.updateNotificationLabel = HyperlinkLabel(this.frame, text="Plugin update available", background=nb.Label().cget("background"),
                                                  url="https://github.com/Thurion/EDSM-RSE-for-EDMC/releases", underline=True)
    updateUI()

    # start update check after frame is initialized to avoid any possible race conditions when generating the event
    thread.start_new_thread(versionCheck, ())

    return this.frame


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not this.enabled and not this.overwrite.get() or is_beta:
        return  # nothing to do here
    if entry["event"] == "FSDJump" or entry["event"] == "Location":
        if "StarPos" in entry:
            this.queue.put((BackgroundWorker.JUMPED_SYSTEM, (tuple(entry["StarPos"]), entry["SystemAddress"])))
    if entry["event"] == "Resurrect":
        # reset radius in case someone died in an area where there are not many available stars (meaning very large radius)
        this.worker.radius = BackgroundWorker.DEFAULT_RADIUS
    if entry["event"] == "NavBeaconScan":
        this.queue.put((BackgroundWorker.NAVBEACON, entry["SystemAddress"]))
