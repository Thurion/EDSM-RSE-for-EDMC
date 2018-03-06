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
import psycopg2
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

VERSION = "1.1"
EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 15

# regex taken from EDTS https://bitbucket.org/Esvandiary/edts
PG_SYSTEM_REGEX = re.compile(r"^(?P<sector>[\w\s'.()/-]+) (?P<l1>[A-Za-z])(?P<l2>[A-Za-z])-(?P<l3>[A-Za-z]) (?P<mcode>[A-Za-z])(?:(?P<n1>\d+)-)?(?P<n2>\d+)$")

OPTIONS_RADIUS             = lambda x: 39+11*(2**x)
DEFAULT_RADIUS             = 2 # key for radius, see OPTIONS_RADIUS
MAX_RADIUS                 = 10
RADIUS_ADJUSTMENT_INCREASE = 15 # increase radius if at most this amount of systems were found
RADIUS_ADJUSTMENT_DECREASE = 100 # decrease the radius if at least this amount of systems were found

# Values for projects
PROJECT_RSE = 1
PROJECT_NAVBACON = 2

# keys for dictionary that stores data from the background thread
# stored in this.lastEventInfo
BG_SYSTEM  = "bg_system"
BG_MESSAGE = "bg_message"

this = sys.modules[__name__]	# For holding module globals

class EliteSystem(object):
    def __init__(self, id, name, x, y, z, uncertainty = None, action = 0):
        self.id          = id
        self.name        = name
        self.x           = x
        self.y           = y
        self.z           = z
        self.uncertainty = uncertainty or 0
        self.distance    = 10000 #set initial value to be out of reach
        self.action      = action
        self.action_text = ""

    @staticmethod
    def calculateDistance(x1, x2, y1, y2, z1, z2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distance = self.calculateDistanceToCoordinates(x, y, z)

    def calculateDistanceToCoordinates(self, x2, y2, z2):
        return self.calculateDistance(self.x, x2, self.y, y2, self.z, z2)

    def removeFromProject(self, projectId):
        self.action = self.action & (~ projectId)

    def calculateDistanceToSystem(self, system2):
        return self.calculateDistanceToCoordinates(system2.x, system2.y, system2.z)

    def __str__(self):
        return "id: {id}, name: {name}, distance: {distance:,.2f}, uncertainty: {uncertainty}".format(id=self.id, name=self.name, distance=self.distance, uncertainty=self.uncertainty)

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
    NAVBEACON = 2

    def __init__(self, queue, radius = DEFAULT_RADIUS):
        Thread.__init__(self)
        self.queue = queue
        self.radius = radius
        self.systemList = list() # nearby systems, sorted by distance
        self.projectsDict = dict()
        self.filter = set() # systems that have been completed


    def adjustRadius(self, numberOfSystems):
        if numberOfSystems <= RADIUS_ADJUSTMENT_INCREASE:
            self.radius += 1
            if self.radius > MAX_RADIUS:
                self.radius = 10
            if __debug__: print("found {0} systems, increasing radius to {1}".format(numberOfSystems, OPTIONS_RADIUS(self.radius)))
        elif numberOfSystems >= RADIUS_ADJUSTMENT_DECREASE:
            self.radius -= 1
            if self.radius < 0:
                self.radius = 0
            if __debug__: print("found {0} systems, decreasing radius to {1}".format(numberOfSystems, OPTIONS_RADIUS(self.radius)))


    def openDatabase(self):
        try:
            self.conn = psycopg2.connect(host="cyberlord.de", port=5432, dbname="edmc_rse_db", user="edmc_rse_user", password="asdfplkjiouw3875948zksmdxnf")
            self.c = self.conn.cursor()
        except Exception as e:
            plug.show_error("EDSM-RSE: Database could not be opened")
            sys.stderr.write("EDSM-RSE: Database could not be opened\n")


    def closeDatabase(self):
        if not hasattr(self, "c") or not self.c:
            return # database not loaded
        self.conn.close()
        self.c = None
        self.conn = None


    def initializeDictionaries(self):
        if not hasattr(self, "c") or not self.c:
            return # database not loaded

        if len(self.projectsDict) == 0:
            self.c.execute("SELECT id,action_text FROM projects")
            self.projectsDict = dict()
            for row in self.c.fetchall():
                id, action_text = row
                self.projectsDict[id] = action_text


    def generateListsFromDatabase(self, x, y, z):
        sql = " ".join([
            "SELECT id, name, x, y, z, uncertainty, action_todo FROM systems WHERE",
            "systems.x BETWEEN %(x1)s AND %(x2)s AND",
            "systems.y BETWEEN %(y1)s AND %(y2)s AND",
            "systems.z BETWEEN %(z1)s AND %(z2)s AND",
            "deleted_at IS NULL;"
        ])
        systems = list()
        # make sure that the between statements are BETWEEN lower limit AND higher limit
        self.c.execute(sql, {
            "x1": x - OPTIONS_RADIUS(self.radius),
            "x2": x + OPTIONS_RADIUS(self.radius),
            "y1": y - OPTIONS_RADIUS(self.radius),
            "y2": y + OPTIONS_RADIUS(self.radius),
            "z1": z - OPTIONS_RADIUS(self.radius),
            "z2": z + OPTIONS_RADIUS(self.radius)
        })
        for row in self.c.fetchall():
            id, name, x2, y2, z2, uncertainty, action = row
            distance = EliteSystem.calculateDistance(x, x2, y, y2, z, z2)
            if distance <= OPTIONS_RADIUS(self.radius):
                eliteSystem = EliteSystem(*row)
                eliteSystem.distance = distance
                eliteSystem.action_text = ", ".join([self.projectsDict[project] for project in self.projectsDict.keys() if (eliteSystem.action & project) == project])
                systems.append(eliteSystem)
 
        # filter out systems that have been completed
        systems = filter(lambda system: system.id not in self.filter, systems)        
        systems.sort(key=lambda l: l.distance)

        self.systemList = systems
        self.adjustRadius(len(self.systemList))
        self.systemDict = dict()


    def removeSystems(self):
        removeMe = filter(lambda x: x.action == 0, self.systemList)
        if __debug__: print("adding {count} systems to removal filter: {systems}".format(count=len(removeMe), systems=[x.name for x in removeMe]))
        self.systemList = [x for x in self.systemList if x not in removeMe]
        for system in removeMe:
            self.filter.add(system.id)


    def queryEDSM(self, systems):
        # TODO: use a cache
        """ returns a set of systems names in lower case with unknown coordinates """
        edsmUrl = "https://www.edsm.net/api-v1/systems?onlyUnknownCoordinates=1&"
        params = list()
        names = set()
        for system in systems:
            if system.uncertainty > 0:
                params.append("systemName[]={name}".format(name=urllib2.quote(system.name)))
        edsmUrl += "&".join(params)

        if __debug__: print("querying EDSM for {} systems".format(len(params)))
        if len(params) > 0:
            try:
                url = urllib2.urlopen(edsmUrl, timeout=10)
                response = url.read()
                edsmJson = json.loads(response)
                for entry in edsmJson:
                    names.add(entry["name"].lower())
                return names
            except:
               # ignore. the EDSM call is not required
               if __debug__: print_exc()
        return set()


    def getSystemFromID(self, id):
        system = filter(lambda x: x.id == id, self.systemList)[:1] # there is only one possible match for ID64, avoid exception being thrown
        if len(system) > 0:
            return system[0]
        else:
            return None


    def handleJumpedSystem(self, coordinates, systemAddress):
        system = self.getSystemFromID(systemAddress)

        if system: # arrived in system without coordinates
            if __debug__: print("arrived in {}".format(system.name))
            system.removeFromProject(PROJECT_RSE)
            self.removeSystems()

        if hasattr(self, "c") and self.c: # make sure the database is accessible
            self.generateListsFromDatabase(*coordinates)
            lowerLimit = 0
            upperLimit = EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
            
            closestSystems = list()
            tries = 0
            while tries < 3 and len(self.systemList) > 0: # no do-while loops...
                closestSystems = self.systemList[lowerLimit:upperLimit]
                edsmResults = self.queryEDSM(closestSystems)
                if len(edsmResults) > 0:
                    # remove systems with coordinates
                    systemsWithCoordinates = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                    for system in systemsWithCoordinates:
                        system.removeFromProject(PROJECT_RSE)
                    self.removeSystems()
                    closestSystems = filter(lambda s: s.name.lower() in edsmResults, closestSystems)
                if len(closestSystems) > 0:
                    # there are still systems in the results -> stop here
                    break
                else:
                    tries += 1
                    lowerLimit += EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
                    upperLimit += EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            this.lastEventInfo = dict()
            if len(self.systemList) > 0:
                this.lastEventInfo[BG_SYSTEM] = self.systemList[0]
            else:
                this.lastEventInfo[BG_MESSAGE] = "No system in range"

            this.frame.event_generate("<<EDSM-RSE_BackgroundWorker>>", when="tail") # calls updateUI in main thread

        else:
            # distances need to be recalculated because we couldn't get a new list from the database
            for system in self.systemList:
                system.updateDistanceToCurrentCommanderPosition(*coordinates)
            self.systemList.sort(key=lambda l: l.distance)


    def handleNavbeacon(self, systemAddress):
        system = self.getSystemFromID(systemAddress)
        if system:
            system.removeFromProject(PROJECT_NAVBACON)
            self.removeSystems()
            this.lastEventInfo = dict()
            this.lastEventInfo[BG_SYSTEM] = self.systemList[0]
            this.frame.event_generate("<<EDSM-RSE_BackgroundWorker>>", when="tail") # calls updateUI in main thread


    def run(self):
        self.openDatabase()
        self.initializeDictionaries()
        self.closeDatabase()
        while True:
            instruction, args = self.queue.get()
            if not instruction:
                break

            if instruction == self.JUMPED_SYSTEM:
                self.openDatabase()
                self.handleJumpedSystem(*args)
                self.closeDatabase()
            elif instruction == self.NAVBEACON:
                self.handleNavbeacon(args) # args is only 1 ID64
            self.queue.task_done()
        self.closeDatabase()
        self.queue.task_done()


def checkTransmissionOptions():
    eddn = (config.getint("output") & config.OUT_SYS_EDDN) == config.OUT_SYS_EDDN
    edsm = config.getint("edsm_out") and 1
    return eddn or edsm


def plugin_start():
    settings = config.getint("EDSM-RSE") or 5 # default setting: radius 0 is currently not selectable
    this.clipboard = tk.IntVar(value=((settings >> 5) & 0x01))
    this.overwrite = tk.IntVar(value=((settings >> 6) & 0x01))

    this.enabled = checkTransmissionOptions()

    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.radius = DEFAULT_RADIUS
    this.worker.start()

    return "EDSM-RSE"


def updateUI(event = None):
    eliteSystem = this.lastEventInfo.get(BG_SYSTEM, None)
    message = this.lastEventInfo.get(BG_MESSAGE, None)
    if (this.enabled or this.overwrite.get()) and eliteSystem:
        this.errorLabel.grid_remove()
        this.unconfirmedSystem.grid(row=0, column=1, sticky=tk.W)
        this.unconfirmedSystem["text"] = eliteSystem.name
        this.unconfirmedSystem["url"] = "https://www.edsm.net/show-system?systemName={}".format(urllib2.quote(eliteSystem.name))
        this.unconfirmedSystem["state"] = "enabled"
        distanceText = u"{distance} Ly".format(distance=Locale.stringFromNumber(eliteSystem.distance, 2))
        if eliteSystem.uncertainty > 0:
            distanceText = distanceText +  u" (\u00B1{uncertainty})".format(uncertainty=eliteSystem.uncertainty)
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
    nb.Checkbutton(frame, variable=this.clipboard, text="Copy system name to clipboard").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    nb.Checkbutton(frame, variable=this.overwrite, text="I use another tool to transmit data to EDSM/EDDN").grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)

    ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=nextRow(), columnspan=2, padx=PADX*2, pady=8, sticky=tk.EW)
    nb.Label(frame, text="Plugin Version: {}".format(VERSION)).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="Open the Github page for this plugin", background=nb.Label().cget("background"), url="https://github.com/Thurion/EDSM-RSE-for-EDMC", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
    HyperlinkLabel(frame, text="A big thanks to EDTS for providing the coordinates.", background=nb.Label().cget("background"), url="http://edts.thargoid.space/", underline=True).grid(row=nextRow(), column=0, columnspan=2, padx=PADX, sticky=tk.W)
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
    this.worker.radius = DEFAULT_RADIUS

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
    tk.Label(this.frame, text="Action:").grid(row=2, column=0, sticky=tk.W)
    this.actionText = tk.Label(this.frame)
    this.actionText.grid(row=2, column=1, sticky=tk.W)

    updateUI()
    return frame


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not this.enabled and not this.overwrite.get() or is_beta:
        return # nothing to do here
    if entry["event"] == "FSDJump" or entry["event"] == "Location":
        if "StarPos" in entry:
            this.queue.put((BackgroundWorker.JUMPED_SYSTEM, (tuple(entry["StarPos"]), entry["SystemAddress"])))
    if entry["event"] == "Resurrect":
        # reset radius in case someone died in an area where there are not many available stars (meaning very large radius)
        this.worker.radius = DEFAULT_RADIUS
    if entry["event"] == "NavBeaconScan":
        this.queue.put((BackgroundWorker.NAVBEACON, entry["SystemAddress"]))
