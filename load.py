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
import math
import json
import re
import urllib2
import time
import sqlite3
from datetime import datetime

from threading import Thread
from Queue import Queue

import Tkinter as tk
import ttk
from ttkHyperlinkLabel import HyperlinkLabel
import myNotebook as nb

from l10n import Locale
from config import config
import plug

if __debug__:
    from traceback import print_exc

VERSION = "1.0 Beta 4"
EDSM_UPDATE_INTERVAL = 3600 # 1 hour. used for EliteSystem
EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 15
DEFAULT_UPDATE_INTERVAL = 1
DEFAULT_RADIUS = 1000
# regex taken from EDTS https://bitbucket.org/Esvandiary/edts
PG_SYSTEM_REGEX = re.compile(r"^(?P<sector>[\w\s'.()/-]+) (?P<l1>[A-Za-z])(?P<l2>[A-Za-z])-(?P<l3>[A-Za-z]) (?P<mcode>[A-Za-z])(?:(?P<n1>\d+)-)?(?P<n2>\d+)$")
MC_VALUES = { "a" : 0, "b" : 1, "c" : 2, "d" : 3, "e" : 4, "f" : 5, "g" : 6, "h" : 7}
OPTIONS_RADIUS = {1 : 100, 2 : 250,  3 : 500, 4 : 750, 5 : 1000, 6: 2000, 7 : 4000}
OPTIONS_INTERVAL = {0 : 1, 1 : 3, 2 : 5, 3 : 7}

# keys for dictionary that stores data from the background thread
# stored in this.lastEventInfo
BG_SYSTEM = "bg_system"
BG_MESSAGE = "bg_message"

this = sys.modules[__name__]	# For holding module globals

class EliteSystem(object):
    def __init__(self, id, name, x, y, z, updated_at = None):
        self.id = id
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.updated_at = updated_at or 0
        self.distance = 10000 #set initial value to be out of reach

    def getUncertainty(self):
        if PG_SYSTEM_REGEX.match(self.name):
            mc = self.name.split(" ")[-1][:1].lower()
            # 1.732051 is the length of the vector (1, 1, 1) (sqrt(3)) and is the distance in the worst case
            return int(((10 * 2 ** MC_VALUES.get(mc, 0)) / 2) * 1.732051) # no need for decimal places here
        return 0

    @staticmethod
    def calculateDistance(x1, x2, y1, y2, z1, z2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distance = self.calculateDistanceToCoordinates(x, y, z)

    def calculateDistanceToCoordinates(self, x2, y2, z2):
        return self.calculateDistance(self.x, x2, self.y, y2, self.z, z2)

    def calculateDistanceToSystem(self, system2):
        return self.calculateDistanceToCoordinates(system2.x, system2.y, system2.z)

    def __str__(self):
        return "id: {id}, name: {name}, distance: {distance:,.2f}, updated: {updated}, uncertainty: {uncertainty}".format(id=self.id, name=self.name, distance=self.distance, updated=self.updated_at, uncertainty=self.getUncertainty())

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id)


class BackgroundWorker(Thread):
    
    # instructions. don't use 0!
    JUMPED_SYSTEM = 1

    def __init__(self, queue, radius = DEFAULT_RADIUS, updateInterval = DEFAULT_UPDATE_INTERVAL):
        Thread.__init__(self)
        self.queue = queue
        self.radius = radius
        self.updateInterval = updateInterval
        self.counter = -1
        self.systemList = list()
        self.systemListHighUncertainty = list()
        self.systemDict = dict()


    def openDatabase(self):
        if not os.path.exists(os.path.join(os.path.dirname(__file__), "systemsWithoutCoordinates.sqlite")):
            plug.show_error("EDSM-RSE: Database could not be opened")
            sys.stderr.write("EDSM-RSE: Database could not be opened\n")
            return
        try:
            self.conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "systemsWithoutCoordinates.sqlite"))
            self.c = self.conn.cursor()
            self.c.execute("SELECT * from version LIMIT 1")
            result = self.c.fetchall()
            this.dbVersion = result[0][0]
        except Exception as e:
            plug.show_error("EDSM-RSE: Database could not be opened")
            sys.stderr.write("EDSM-RSE: Database could not be opened\n")


    def closeDatabase(self):
        if not hasattr(self, "c") or not self.c:
            return # database not loaded
        self.conn.close()


    def initializeDictionaries(self):
        if not hasattr(self, "c") or not self.c:
            return # database not loaded
        self.realNameToPg = dict()
        self.pgToRealName = dict()
        for row in self.c.execute("SELECT * FROM duplicates"):
            _, realName, pgName = row
            self.realNameToPg.setdefault(realName.lower(), list())
            self.realNameToPg.get(realName.lower(), list()).append(pgName)
            self.pgToRealName[pgName.lower()] = realName


    def generateListsFromDatabase(self, x, y, z):
        sql = "SELECT * FROM systems WHERE systems.x BETWEEN ? AND ? AND systems.y BETWEEN ? AND ? AND systems.z BETWEEN ? AND ?"
        systems = list()
        # make sure that the between statements are BETWEEN lower limit AND higher limit
        self.c.execute(sql, (x - self.radius, x + self.radius, y - self.radius, y + self.radius, z - self.radius, z + self.radius))
        for row in self.c.fetchall():
            _, name, x2, y2, z2, _ = row
            if name in self.pgToRealName: continue # TODO handle dupe systems. ignore them for now
            distance = EliteSystem.calculateDistance(x, x2, y, y2, z, z2)
            if distance <= self.radius:
                eliteSystem = EliteSystem(*row)
                eliteSystem.distance = distance
                systems.append(eliteSystem)
        systems.sort(key=lambda l: l.distance)

        self.systemList = systems
        self.systemDict = dict()
        for system in self.systemList:
            self.systemDict.setdefault(system.name.lower(), system)
        for system in self.systemListHighUncertainty:
            self.systemDict.setdefault(system.name.lower(), system)


    def updateTimeForSystems(self, systems, t):
        if __debug__: print("updateTimeForSystems for {} systems".format(len(systems)))
        for system in systems:
            system.updated_at = t
            self.c.execute("UPDATE systems SET last_checked = ? WHERE systems.id = ?", (t, system.id))
        if (systems):
            self.conn.commit() # commit only if the list contained items


    def removeSystemsFromDatabase(self, systems):
        for system in systems:
            self.c.execute("DELETE FROM systems WHERE systems.id = ?", (system.id,))
        self.conn.commit()


    def removeSystems(self, systems):
        if __debug__: print("removing {} systems".format(len(systems)))
        self.systemList = filter(lambda x: x not in systems, self.systemList)
        self.systemListHighUncertainty = filter(lambda x: x not in systems, self.systemListHighUncertainty)

        for system in systems:
            self.systemDict.pop(system.name.lower(), None)


    def queryEDSM(self, systems):
        """ returns a set of systems names in lower case with known coordinates """
        # TODO handle dupes
        edsmUrl = "https://www.edsm.net/api-v1/systems?onlyKnownCoordinates=1&"
        params = list()
        currentTime = int(time.time())
        systemsToUpdateTime = list()
        for system in systems:
            if (currentTime - system.updated_at) > EDSM_UPDATE_INTERVAL:
                params.append("systemName[]={name}".format(name=urllib2.quote(system.name)))
                systemsToUpdateTime.append(system)
        edsmUrl += "&".join(params)
        if __debug__: print("querying EDSM for {} systems".format(len(params)))
        if len(params) > 0:
            try:
                url = urllib2.urlopen(edsmUrl, timeout=5)
                response = url.read()
                edsmJson = json.loads(response)
                names = set()
                for entry in edsmJson:
                    names.add(entry["name"].lower())
                self.updateTimeForSystems(systemsToUpdateTime, currentTime)
                return names
            except:
               # ignore. the EDSM call is not required
               if __debug__: print_exc()
        return set()


    def handleJumpedSystem(self, coordinates, starName):
        if not hasattr(self, "c") or not self.c:
            return # no database. do nothing

        self.counter += 1
        tick = self.counter % self.updateInterval == 0
        if tick: 
            if __debug__: print("interval tick")
            # interval -> update systems
            self.generateListsFromDatabase(*coordinates)
            lowerLimit = 0
            upperLimit = EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
            
            closestSystems = list()
            tries = 0
            while tries < 3 and len(self.systemList) > 0: # no do-while loops...
                closestSystems = self.systemList[lowerLimit:upperLimit]
                currentTime = int(time.time())
                edsmResults = self.queryEDSM(closestSystems)
                if len(edsmResults) > 0:
                    # remove systems with coordinates
                    systemsWithCoordinates = filter(lambda s: s.name.lower() in edsmResults, closestSystems)
                    self.removeSystemsFromDatabase(systemsWithCoordinates)
                    self.removeSystems(systemsWithCoordinates)
                    closestSystems = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                if len(closestSystems) > 0:
                    # there are still systems in the results -> stop here
                    break
                else:
                    tries += 1
                    lowerLimit += EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
                    upperLimit += EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            this.lastEventInfo = dict()
            if len(closestSystems) > 0:
                closestSystem = closestSystems[0]
                if closestSystem.getUncertainty() > self.radius and closestSystem not in self.systemListHighUncertainty:
                    self.systemListHighUncertainty.append(closestSystem)
                this.lastEventInfo[BG_SYSTEM] = closestSystem
            else:
                this.lastEventInfo[BG_MESSAGE] = "No system in range"

            this.frame.event_generate('<<EDSM-RSE_BackgroundWorker>>', when="tail") # calls updateUI in main thread

        if starName.lower() in self.systemDict: # arrived in system without coordinates
            # TODO handle dupes
            if __debug__: print("arrived in {}".format(starName))
            system = self.systemDict.get(starName.lower(), None)
            if system:
                self.removeSystemsFromDatabase([system])
                self.removeSystems([system])

            if not tick:
                # distances need to be recalculated
                for system in self.systemList:
                    system.updateDistanceToCurrentCommanderPosition(*coordinates)
                self.systemList.sort(key=lambda l: l.distance)
            this.lastEventInfo = dict()
            if len(self.systemList) > 0:
                this.lastEventInfo[BG_SYSTEM] = self.systemList[0]
            else:
                this.lastEventInfo[BG_MESSAGE] = "No system in range"
            this.frame.event_generate('<<EDSM-RSE_BackgroundWorker>>', when="tail") # calls updateUI in main thread


    def run(self):
        self.openDatabase()
        self.initializeDictionaries()
        while True:
            instruction, args = self.queue.get()
            if not instruction:
                break

            if instruction == self.JUMPED_SYSTEM:
                self.handleJumpedSystem(*args)
            self.queue.task_done()
        self.closeDatabase()
        self.queue.task_done()


def checkTransmissionOptions():
    eddn = (config.getint("output") & config.OUT_SYS_EDDN) == config.OUT_SYS_EDDN
    edsm = config.getint('edsm_out') and 1
    return eddn or edsm


def plugin_start():
    this.dbVersion = 0
    settings = config.getint("EDSM-RSE") or 5 # default setting: radius 0 is currently not selectable
    this.radius = tk.IntVar(value=(settings & 0x07))
    this.updateInterval = tk.IntVar(value=((settings >> 3) & 0x03))
    this.clipboard = tk.IntVar(value=((settings >> 5) & 0x01))
    this.overwrite = tk.IntVar(value=((settings >> 6) & 0x01))

    this.enabled = checkTransmissionOptions()

    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.radius = OPTIONS_RADIUS.get(this.radius.get(), DEFAULT_RADIUS) # number does not translate into radius. this step is required
    this.worker.updateInterval = OPTIONS_INTERVAL.get(this.updateInterval.get(), DEFAULT_UPDATE_INTERVAL) # number translates directly to interval, global variable could be used
    this.worker.start()

    return 'EDSM-RSE'


def updateUI(event = None):
    eliteSystem = this.lastEventInfo.get(BG_SYSTEM, None)
    message = this.lastEventInfo.get(BG_MESSAGE, None)
    if (this.enabled or this.overwrite.get()) and eliteSystem:
        this.errorLabel.grid_remove()
        this.unconfirmedSystem.grid(row=0, column=1, sticky=tk.W)
        this.unconfirmedSystem["text"] = eliteSystem.name
        this.unconfirmedSystem["url"] = "https://www.edsm.net/show-system?systemName={}".format(urllib2.quote(eliteSystem.name))
        this.unconfirmedSystem["state"] = "enabled"
        this.distanceValue["text"] = u"{distance} Ly (\u00B1{uncertainty})".format(distance=Locale.stringFromNumber(eliteSystem.distance, 2), uncertainty=eliteSystem.getUncertainty())
        if this.clipboard.get():
            this.frame.clipboard_clear()
            this.frame.clipboard_append(eliteSystem.name)
    else:
        this.unconfirmedSystem.grid_remove()
        this.errorLabel.grid(row=0, column=1, sticky=tk.W)
        this.distanceValue["text"] = "?"
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
    frame.columnconfigure(1, weight=1)
    nb.Label(frame, text="Search Radius in Ly:").grid(row=0, column=0, padx=PADX, pady=(8,0), sticky=tk.W)
    nb.Label(frame, text="Update Every x Jumps:").grid(row=0, column=1, padx=PADX, pady=(8,0), sticky=tk.W)

    row  = rowInterval = 1
    values = sorted(OPTIONS_RADIUS.keys())
    for value in values:
        nb.Radiobutton(frame, variable=this.radius, value=value, text=str(OPTIONS_RADIUS.get(value, ""))).grid(row=row, column=0, padx=PADX*4, sticky=tk.EW)
        row += 1
    
    values = sorted(OPTIONS_INTERVAL.keys())
    for value in values:
        nb.Radiobutton(frame, variable=this.updateInterval, value=value, text=str(OPTIONS_INTERVAL.get(value, ""))).grid(row=rowInterval, column=1, padx=PADX*4, sticky=tk.EW)
        rowInterval += 1
    
    nb.Label(frame).grid(row=nextRow()) #spacer
    nb.Checkbutton(frame, variable=this.clipboard, text="Copy system name to clipboard").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Checkbutton(frame, variable=this.overwrite, text="I use another tool to transmit data to EDSM/EDDN").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)

    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=nextRow(), columnspan=2, padx=PADX*2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Plugin Version: {}".format(VERSION)).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Label(frame, text="Database created: {}".format(datetime.fromtimestamp(this.dbVersion))).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="Open the Github page for this plugin", background=nb.Label().cget("background"), url="https://github.com/Thurion/EDSM-RSE-for-EDMC", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="A big thanks to EDTS for providing the coordinates.", background=nb.Label().cget("background"), url="http://edts.thargoid.space/", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    return frame


def prefs_changed():
    # bits are as follows:
    # 0-3 radius
    # 4-5 interval
    # 6: copy to clipboard
    # 7: overwrite enabled status
    settings = this.radius.get() | (this.updateInterval.get() << 3) | (this.clipboard.get() << 5) | (this.overwrite.get() << 6)
    config.set("EDSM-RSE", settings)
    this.enabled = checkTransmissionOptions()
    this.worker.radius = OPTIONS_RADIUS.get(this.radius.get(), DEFAULT_RADIUS) # number does not translate into radius. this step is required
    this.worker.updateInterval = OPTIONS_INTERVAL.get(this.updateInterval.get(), DEFAULT_UPDATE_INTERVAL) # number translates directly to interval, global variable could be used
    this.worker.counter = 0

    updateUI()


def plugin_app(parent):
    this.frame = tk.Frame(parent)
    this.frame.bind_all("<<EDSM-RSE_BackgroundWorker>>", updateUI)
    this.frame.columnconfigure(1, weight=1)
    tk.Label(this.frame, text="Unconfirmed:").grid(row=0, column=0, sticky=tk.W)
    this.unconfirmedSystem = HyperlinkLabel(frame, compound=tk.RIGHT, popup_copy = True)
    this.errorLabel = tk.Label(frame)
    tk.Label(this.frame, text="Distance:").grid(row=1, column=0, sticky=tk.W)
    this.distanceValue = tk.Label(this.frame)
    this.distanceValue.grid(row=1, column=1, sticky=tk.W)
    this.lastEventInfo = dict()

    updateUI()
    return frame


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not this.enabled or is_beta:
        return # nothing to do here
    if entry["event"] == "FSDJump" or entry["event"] == "Location":
        if "StarPos" in entry:
            this.queue.put((BackgroundWorker.JUMPED_SYSTEM, (tuple(entry["StarPos"]), entry["StarSystem"])))
