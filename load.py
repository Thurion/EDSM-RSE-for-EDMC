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

this = sys.modules[__name__]	# For holding module globals

class EliteSystem(object):
    def __init__(self, id, name, x, y, z, updated_at):
        self.id = id
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.updated_at = updated_at
        self.distanceSquared = 10000 ** 2

    @staticmethod
    def calculateDistanceSquared(x1, x2, y1, y2, z1, z2):
        return (x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distanceSquared = self.calculateDistanceSquaredWithCoordinates(x, y, z)

    def calculateDistanceSquaredWithCoordinates(self, x2, y2, z2):
        return self.calculateDistanceSquared(self.x, x2, self.y, y2, self.z, z2)

    def calculateDistance(self, system2):
        return math.sqrt(self.calculateDistanceSquaredWithCoordinates(system2.x, system2.y, system2.z))

    def getNormalDistance(self):
        return math.sqrt(self.distanceSquared)

    def __str__(self):
        return "id: {id}, name: {name}, distance^2: {distance:,.2f}, updated: {updated}".format(id=self.id, name=self.name, distance=self.distanceSquared, updated=self.updated_at)

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
    def __init__(self, queue, radius = 1000):
       Thread.__init__(self)
       self.queue = queue
       self.radius = radius

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

    def fetchSystemsInRadiusAroundPoint(self, x, y, z):
        sql = "SELECT * FROM systems WHERE systems.x BETWEEN ? AND ? AND systems.y BETWEEN ? AND ? AND systems.z BETWEEN ? AND ?"
        # make sure that the between statements are BETWEEN lower limit AND higher limit
        systems = list()
        refSystem = EliteSystem
        for row in self.c.execute(sql, (x - self.radius, x + self.radius, y - self.radius, y + self.radius, z - self.radius, z + self.radius)):
            _, _, x2, y2, z2, _ = row
            distance = EliteSystem.calculateDistanceSquared(x, x2, y, y2, z, z2)
            if distance <= self.radius ** 2:
                eliteSystem = EliteSystem(*row)
                eliteSystem.distanceSquared = distance
                systems.append(eliteSystem)
        systems.sort(key=lambda l: l.distanceSquared)
        return systems

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

