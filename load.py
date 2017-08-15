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
import urllib2
import webbrowser
import sqlite3
import time
import ttk
import Tkinter as tk
import re
from threading import Thread
from Queue import Queue
from l10n import Locale
from config import config
import myNotebook as nb

VERSION = "0.1 Beta"
EDSM_UPDATE_INTERVAL = 3600 # 1 hour. used for EliteSystem
EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 25
DEFAULT_UPDATE_INTERVAL = 1
DEFAULT_RADIUS = 1000
# regex taken from EDTS https://bitbucket.org/Esvandiary/edts
PG_SYSTEM_REGEX = re.compile(r"^(?P<sector>[\w\s'.()/-]+) (?P<l1>[A-Za-z])(?P<l2>[A-Za-z])-(?P<l3>[A-Za-z]) (?P<mcode>[A-Za-z])(?:(?P<n1>\d+)-)?(?P<n2>\d+)$")
MC_VALUES = { "a" : 0, "b" : 1, "c" : 2, "d" : 3, "e" : 4, "f" : 5, "g" : 6, "h" : 7}

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
            return (10 * 2 ** MC_VALUES.get(mc, 0)) / 2
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
        return "id: {id}, name: {name}, distance^2: {distance:,.2f}, updated: {updated}".format(id=self.id, name=self.name, distance=self.distance, updated=self.updated_at)

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
    
    # instructions
    JUMPED_SYSTEM = 0

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
        self.conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "systemsWithoutCoordinates.sqlite"))
        self.c = self.conn.cursor()

    def initializeDictionaries(self):
        self.realNameToPg = dict()
        self.pgToRealName = dict()
        for row in self.c.execute("SELECT * FROM duplicates"):
            _, realName, pgName = row
            self.realNameToPg.setdefault(realName.lower(), list())
            self.realNameToPg.get(realName.lower(), list()).append(pgName)
            self.pgToRealName[pgName.lower()] = realName

    def fetchAndUpdateSystemsInRadiusAroundPoint(self, x, y, z):
        sql = "SELECT * FROM systems WHERE systems.x BETWEEN ? AND ? AND systems.y BETWEEN ? AND ? AND systems.z BETWEEN ? AND ?"
        # make sure that the between statements are BETWEEN lower limit AND higher limit
        systems = list()
        self.c.execute(sql, (x - self.radius, x + self.radius, y - self.radius, y + self.radius, z - self.radius, z + self.radius))
        for row in self.c.fetchall():
            _, _, x2, y2, z2, _ = row
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

    def removeSystemsFromDatabase(self, systems):
        for system in systems:
            self.c.execute("DELETE FROM systems WHERE systems.id = ?", (system.id))
        self.conn.commit()

    def removeSystems(self, systems):
        self.systemList = filter(lambda x: x not in systems, self.systemList)
        self.systemListHighUncertainty = filter(lambda x: x not in systems, self.systemListHighUncertainty)

        for system in systems:
            self.systemDict.pop(system.name.lower(), None)

    def run(self):
        self.openDatabase()
        self.initializeDictionaries()
        while True:
            instruction, args = self.queue.get()
            self.queue.task_done()


def plugin_start():
    this.queue = Queue()
    this.worker = BackgroundWorker(this.queue)
    this.worker.name = "EDSM-RSE Background Worker"
    this.worker.daemon = True
    this.worker.start()

    return 'EDSM-RSE'

def plugin_prefs(parent):
    frame = nb.Frame(parent)
    return frame

def plugin_app(parent):
    frame = tk.Frame(parent)
    return frame

def journal_entry(cmdr, system, station, entry, state):
    pass

