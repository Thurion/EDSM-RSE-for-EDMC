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
import os
import time
import logging

from queue import Queue
from urllib.parse import quote
from typing import Dict, Union

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox as tkMessageBox
import myNotebook as nb
from ttkHyperlinkLabel import HyperlinkLabel
from config import config, appname
from l10n import Locale

from RseData import RseData, EliteSystem
from Backgroundworker import BackgroundWorker
import BackgroundTask as BackgroundTask

logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")
this = sys.modules[__name__]  # For holding module globals

this.edmc_has_logging_support = True

if not logger.hasHandlers():
    this.edmc_has_logging_support = False
    level = logging.INFO  # So logger.info(...) is equivalent to print()

    logger.setLevel(logging.INFO)
    logger_channel = logging.StreamHandler()
    logger_channel.setLevel(level)
    logger_formatter = logging.Formatter(f"%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d:%(funcName)s: %(message)s")
    logger_formatter.default_time_format = "%Y-%m-%d %H:%M:%S"
    logger_formatter.default_msec_format = "%s.%03d"
    logger_channel.setFormatter(logger_formatter)
    #logger.addHandler(this.logger_channel)


this.CONFIG_IGNORED_PROJECTS = "EDSM-RSE_ignoredProjects"
this.CONFIG_MAIN = "EDSM-RSE"

this.rseData = None  # type: Union[RseData, None]
this.systemCreated = False  # initialize with false in case someone uses an older EDMC version that does not call edsm_notify_system()
this.enabled = False  # plugin configured correctly and therefore enabled
this.currentSystem = None  # type: Union[EliteSystem, None] # current system
this.commander = None  # name of current commander

this.worker = None  # type: Union[BackgroundWorker, None]
this.queue = None  # type: Union[Queue, None] # queue used by the background worker

# ui elements in options
this.debug = None  # Type: Union[tk.BooleanVar, None] # toggle debug messages to eddb log
this.clipboard = None  # type: Union[tk.BooleanVar, None] # copy system name to clipboard
this.overwrite = None  # type: Union[tk.BooleanVar, None] # overwrite disabled state (EDSM/EDDN disabled)
this.edsmBodyCheck = None  # type: Union[tk.BooleanVar, None] # in settings; compare total number of bodies to the number known to EDSM
this.systemScanned = False  # variable to prevent spamming the EDSM API
this.ignoredProjectsCheckboxes = dict()  # type: Dict[int, tk.BooleanVar]

# ui elements in main window
this.errorLabel = None  # type: Union[tk.Label, None] # show if plugin can't work (EDSM/EDDN disabled)
this.distanceValue = None  # type: Union[tk.Label, None] # distance to system
this.actionText = None  # type: Union[tk.Label, None] # task to do
this.edsmBodyFrame = None  # type: Union[tk.Frame, None] # frame containing all UI elements for EDSM body count
this.edsmBodyCountText = None  # type: Union[tk.Label, None] # text of information about bodies known to EDSM
this.unconfirmedSystem = None  # type: Union[RseHyperlinkLabel, None] # display name of system that needs checking
this.updateNotificationLabel = None  # type: Union[HyperlinkLabel, None]


class RseHyperlinkLabel(HyperlinkLabel):

    def __init__(self, master=None, **kw):
        super(RseHyperlinkLabel, self).__init__(master, **kw)
        self.menu.add_command(label=_("Ignore once"), command=self.ignore_once)
        self.menu.add_command(label=_("Ignore this session"), command=self.ignore_temporarily)
        self.menu.add_command(label=_("Ignore for 24 hours"), command=self.ignore_for24)
        self.menu.add_command(label=_("Ignore indefinitely"), command=self.ignore_indefinitely)

    def ignore_once(self):
        this.queue.put(BackgroundTask.IgnoreSystemTask(this.rseData, self["text"], True))

    def ignore_temporarily(self):
        this.queue.put(BackgroundTask.IgnoreSystemTask(this.rseData, self["text"]))

    def ignore_for24(self):
        this.queue.put(BackgroundTask.IgnoreSystemTask(this.rseData, self["text"], False, int(time.time() + 24 * 3600)))

    def ignore_indefinitely(self):
        this.queue.put(BackgroundTask.IgnoreSystemTask(this.rseData, self["text"], 2 ** 31 - 1))


def check_transmission_options():
    eddn = (config.get_int("output") & config.OUT_SYS_EDDN) == config.OUT_SYS_EDDN
    edsm = config.get_int("edsm_out") and 1
    return eddn or edsm


def plugin_start(plugin_dir):
    this.rseData = RseData(plugin_dir)
    settings = config.get_int(this.CONFIG_MAIN) or 0  # default setting
    this.rseData.ignored_projects_flags = config.get_int(this.CONFIG_IGNORED_PROJECTS)
    this.clipboard = tk.BooleanVar(value=((settings >> 5) & 0x01))
    this.overwrite = tk.BooleanVar(value=((settings >> 6) & 0x01))
    this.edsmBodyCheck = tk.BooleanVar(value=not ((settings >> 7) & 0x01))  # invert to be on by default
    this.debug = tk.BooleanVar(value=((settings >> 8) & 0x01))
    if this.debug.get():
        level = logging.DEBUG
        logger.setLevel(level)
        for handler in logger.handlers:
            handler.setLevel(level)
    
    this.enabled = check_transmission_options()

    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue, this.rseData)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.radiusExponent = RseData.DEFAULT_RADIUS_EXPONENT
    this.worker.start()

    logger.debug("Debug messages are enabled.")
    logger.debug("Python Version: {0}.".format(sys.version))
    return RseData.PLUGIN_NAME


def plugin_start3(plugin_dir):
    plugin_start(plugin_dir)


def update_ui_unconfirmed_system(event=None):
    elite_system = this.rseData.last_event_info.get(RseData.BG_RSE_SYSTEM, None)  # type: EliteSystem
    message = this.rseData.last_event_info.get(RseData.BG_RSE_MESSAGE, None)
    if (this.enabled or this.overwrite.get()) and elite_system:
        this.errorLabel.grid_remove()
        this.unconfirmedSystem.grid(row=0, column=1, sticky=tk.W)
        this.unconfirmedSystem["text"] = elite_system.name
        this.unconfirmedSystem["url"] = "https://www.edsm.net/show-system?systemName={}".format(quote(elite_system.name))
        this.unconfirmedSystem["state"] = "enabled"
        distance_text = u"{distance} Ly".format(distance=Locale.string_from_number(elite_system.distance, 2))
        if elite_system.uncertainty > 0:
            distance_text = distance_text + u" (\u00B1{uncertainty})".format(uncertainty=elite_system.uncertainty)
        this.distanceValue["text"] = distance_text
        this.actionText["text"] = elite_system.get_action_text()
        if this.clipboard.get():
            this.frame.clipboard_clear()
            this.frame.clipboard_append(elite_system.name)
    else:
        this.unconfirmedSystem.grid_remove()
        this.errorLabel.grid(row=0, column=1, sticky=tk.W)
        this.distanceValue["text"] = "?"
        this.actionText["text"] = "?"
        if not this.enabled and not this.overwrite.get():
            this.errorLabel["text"] = "EDSM/EDDN is disabled"
        else:
            this.errorLabel["text"] = message or "?"


def update_ui_edsm_body_count(event=None):
    message = this.rseData.last_event_info.get(RseData.BG_EDSM_BODY, None)
    if this.edsmBodyCheck.get():
        if message:
            this.edsmBodyCountText["text"] = message
        else:
            this.edsmBodyCountText["text"] = "?"
        this.edsmBodyFrame.grid(row=11, columnspan=2, sticky=tk.EW)
    else:
        this.edsmBodyFrame.grid_remove()


def plugin_close():
    # Signal thread to close and wait for it
    this.queue.put(None)
    this.worker.join()
    this.worker = None


def clear_scanned_systems_cache_callback(cache_type: int, name: str):
    # called when clicked on the clear cache of scanned systems button in settings
    result = tkMessageBox.askquestion("Delete " + name, "Do you really want to delete all {}?\nThis cannot be undone.".format(name), icon='warning')
    if result == tkMessageBox.YES:
        this.queue.put(BackgroundTask.DeleteSystemsFromCacheTask(this.rseData, cache_type))


def plugin_prefs(parent, cmdr, is_beta):
    PADX = 5

    frame = nb.Frame(parent)
    frame.columnconfigure(0, weight=1)

    nb.Checkbutton(frame, variable=this.edsmBodyCheck,
                   text="Display number of bodies known to EDSM in current system").grid(padx=PADX, sticky=tk.W)

    # enable projects
    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Please choose which projects to enable").grid(padx=PADX, sticky=tk.W)
    for rseProject in this.rseData.projects_dict.values():
        invertedFlag = not (this.rseData.ignored_projects_flags & rseProject.project_id == rseProject.project_id)
        variable = this.ignoredProjectsCheckboxes.setdefault(rseProject.project_id, tk.BooleanVar(value=invertedFlag))
        text = rseProject.name
        if not rseProject.enabled:
            text += " (globally disabled)"
        nb.Checkbutton(frame, variable=variable, text=text).grid(padx=PADX, sticky=tk.W)
        nb.Label(frame, text=rseProject.explanation).grid(padx=PADX * 4, sticky=tk.W)

    # overwrite disabled state when EDDN/EDSM is off in EDMC
    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Checkbutton(frame, variable=this.clipboard,
                   text="Copy system name to clipboard after jump").grid(padx=PADX, sticky=tk.W)
    nb.Checkbutton(frame, variable=this.overwrite,
                   text="I use another tool to transmit data to EDSM/EDDN").grid(padx=PADX, sticky=tk.W)

    # clear caches
    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Clear caches").grid(padx=PADX, sticky=tk.W)
    clearCachesFrame = nb.Frame(frame)
    clearCachesFrame.grid(padx=PADX * 2, pady=8, sticky=tk.EW)
    frame.columnconfigure(2, weight=1)
    nb.Button(clearCachesFrame, text="Fully scanned systems", command=lambda: clear_scanned_systems_cache_callback(RseData.CACHE_FULLY_SCANNED_BODIES, "fully scanned systems"))\
        .grid(padx=PADX, sticky=tk.W, row=0, column=0)
    nb.Button(clearCachesFrame, text="Ignored systems", command=lambda: clear_scanned_systems_cache_callback(RseData.CACHE_IGNORED_SYSTEMS, "ignored systems")) \
        .grid(padx=PADX, sticky=tk.W, row=0, column=1)

    # links
    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(padx=PADX * 2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Plugin Version: {}".format(RseData.VERSION)).grid(padx=PADX, sticky=tk.W)
    if not this.edmc_has_logging_support:
        nb.Checkbutton(frame, variable=this.debug,
                       text="Verbose Logging").grid(padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="Open the Github page for this plugin", background=nb.Label().cget("background"),
                   url="https://github.com/Thurion/EDSM-RSE-for-EDMC", underline=True).grid(padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="A big thanks to EDTS for providing the coordinates.", background=nb.Label().cget("background"),
                   url="http://edts.thargoid.space/", underline=True).grid(padx=PADX, sticky=tk.W)
    return frame


def prefs_changed(cmdr, is_beta):
    # bits are as follows:
    # 0-3 radius # not used anymore
    # 4-5 interval, not used anymore
    # 6: copy to clipboard
    # 7: overwrite enabled status
    # 8: EDSM body check, value inverted
    # 9: Debug
    settings = (this.clipboard.get() << 5) | (this.overwrite.get() << 6) | ((not this.edsmBodyCheck.get()) << 7) | (this.debug.get() << 8)
    config.set(this.CONFIG_MAIN, settings)
    this.enabled = check_transmission_options()
    this.rseData.radius_exponent = RseData.DEFAULT_RADIUS_EXPONENT

    old_flags = this.rseData.ignored_projects_flags
    for k, v in this.ignoredProjectsCheckboxes.items():
        if not v.get():  # inverted, user wants to ignore this project
            this.rseData.ignored_projects_flags = this.rseData.ignored_projects_flags | k
        else:
            this.rseData.ignored_projects_flags = this.rseData.ignored_projects_flags & (0xFFFFFFFF - k)  # DWord = 32 bit

    if old_flags != this.rseData.ignored_projects_flags:
        this.rseData.radius = RseData.DEFAULT_RADIUS_EXPONENT  # reset radius just in case
        if this.currentSystem:
            this.rseData.system_list = list()  # clear list in case there is no system nearby
            this.queue.put(BackgroundTask.JumpedSystemTask(this.rseData, this.currentSystem))

    config.set(this.CONFIG_IGNORED_PROJECTS, this.rseData.ignored_projects_flags)

    if this.debug.get():
        level = logging.DEBUG
    else:
        level = logging.INFO
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)
    logger.debug("RSE Debug messages are enabled.")

    update_ui_unconfirmed_system()
    update_ui_edsm_body_count()


def show_update_notification(event=None):
    updateVersionInfo = this.rseData.last_event_info.get(RseData.BG_UPDATE_JSON, None)
    if updateVersionInfo:
        url = updateVersionInfo["url"]
        text = "Plugin update to {version} available".format(version=updateVersionInfo["version"])
    else:
        url = "https://github.com/Thurion/EDSM-RSE-for-EDMC/releases"
        text = "Plugin update available"
    this.updateNotificationLabel["url"] = url
    this.updateNotificationLabel["text"] = text
    this.updateNotificationLabel.grid(row=99, column=0, columnspan=2, sticky=tk.W)  # always put in last row


def plugin_app(parent):
    this.frame = tk.Frame(parent)
    this.frame.bind_all(RseData.EVENT_RSE_BACKGROUNDWORKER, update_ui_unconfirmed_system)
    this.frame.bind_all(RseData.EVENT_RSE_UPDATE_AVAILABLE, show_update_notification)
    this.frame.bind_all(RseData.EVENT_RSE_EDSM_BODY_COUNT, update_ui_edsm_body_count)

    this.rseData.set_frame(this.frame)

    this.frame.columnconfigure(1, weight=1)
    tk.Label(this.frame, text="Target:").grid(row=0, column=0, sticky=tk.W)
    this.unconfirmedSystem = RseHyperlinkLabel(this.frame, compound=tk.RIGHT, popup_copy=True)
    this.errorLabel = tk.Label(this.frame)
    tk.Label(this.frame, text="Distance:").grid(row=1, column=0, sticky=tk.W)
    this.distanceValue = tk.Label(this.frame)
    this.distanceValue.grid(row=1, column=1, sticky=tk.W)
    tk.Label(this.frame, text="Todo:").grid(row=2, column=0, sticky=tk.W)
    this.actionText = tk.Label(this.frame)
    this.actionText.grid(row=2, column=1, sticky=tk.W)

    this.edsmBodyFrame = tk.Frame(this.rseData.frame)
    this.edsmBodyFrame.columnconfigure(1, weight=1)
    tk.Frame(this.edsmBodyFrame, highlightthickness=1).grid(row=0, pady=3, columnspan=2, sticky=tk.EW)  # separator
    tk.Label(this.edsmBodyFrame, text="EDSM Bodies:").grid(row=1, column=0, sticky=tk.W)
    this.edsmBodyCountText = tk.Label(this.edsmBodyFrame)
    this.edsmBodyCountText["text"] = "?"
    this.edsmBodyCountText.grid(row=1, column=1, sticky=tk.W)

    this.updateNotificationLabel = HyperlinkLabel(this.frame, text="Plugin update available", background=nb.Label().cget("background"),
                                                  url="https://github.com/Thurion/EDSM-RSE-for-EDMC/releases", underline=True)
    update_ui_unconfirmed_system()
    update_ui_edsm_body_count()

    # start update check after frame is initialized to avoid any possible race conditions when generating the event
    this.queue.put(BackgroundTask.VersionCheckTask(this.rseData))

    return this.frame


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not this.enabled and not this.overwrite.get() or is_beta:
        return  # nothing to do here

    if this.commander != cmdr:
        # user switched commanders, reset the list of systems
        logger.debug("New commander detected: {cmdr}; resetting radius and clearing nearby systems.".format(cmdr=cmdr))
        this.commander = cmdr
        this.rseData.system_list = list()
        this.rseData.radius_exponent = RseData.DEFAULT_RADIUS_EXPONENT

    if entry["event"] in ["FSDJump", "Location", "CarrierJump", "StartUp"]:
        if entry["SystemAddress"] in this.rseData.get_cached_set(RseData.CACHE_FULLY_SCANNED_BODIES):
            this.edsmBodyCountText["text"] = "System complete"
            this.systemScanned = True
        else:
            this.edsmBodyCountText["text"] = "Use discovery scanner"
            this.systemScanned = False
        if "StarPos" in entry:
            this.currentSystem = EliteSystem(entry["SystemAddress"], entry["StarSystem"], *entry["StarPos"])
            this.queue.put(BackgroundTask.JumpedSystemTask(this.rseData, this.currentSystem))

    if entry["event"] == "Resurrect":
        # reset radius in case someone died in an area where there are not many available stars (meaning very large radius)
        this.rseData.system_list = list()
        this.rseData.radius = RseData.DEFAULT_RADIUS_EXPONENT

    if entry["event"] == "NavBeaconScan":
        this.queue.put(BackgroundTask.NavbeaconTask(this.rseData, entry["SystemAddress"]))

    if entry["event"] == "FSSDiscoveryScan" and this.edsmBodyCheck.get():
        if not this.systemScanned:
            if this.systemCreated:
                this.edsmBodyCountText["text"] = "0/{}".format(entry["BodyCount"])
            else:
                this.queue.put(BackgroundTask.FSSDiscoveryScanTask(this.rseData, system, entry["BodyCount"], entry["Progress"]))
        this.systemScanned = True

    if entry["event"] == "FSSAllBodiesFound":
        this.systemScanned = True
        this.queue.put(BackgroundTask.FSSAllBodiesFoundTask(this.rseData, entry["SystemAddress"], this.edsmBodyCheck.get()))


def edsm_notify_system(reply):
    if reply.get("systemCreated"):
        this.systemCreated = True
    else:
        this.systemCreated = False
