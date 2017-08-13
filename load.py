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
import ttk
import math
import json
import Tkinter as tk
import urllib2
import webbrowser
from threading import Thread
from l10n import Locale
from config import config
import myNotebook as nb

VERSION = "0.1 Beta"

class EliteSystem(object):
    def __init__(self, id, name, x, y, z, updated_at):
        self.id = id
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.updated_at = updated_at
        self.distanceSquared = 10000 ** 2

    def updateDistanceToCurrentCommanderPosition(self, coordinates):
        self.distanceSquared = self.calculateDistanceSquaredWithCoordinates(*coordinates)

    def calculateDistanceSquaredWithCoordinates(self, x2, y2, z2):
        return (self.x - x2) ** 2 + (self.y - y2) ** 2 + (self.z - z2) ** 2

    def calculateDistanceSquared(self, system2):
        return (self.x - system2.x) ** 2 + (self.y - system2.y) ** 2 + (self.z - system2.z) ** 2

    def calculateDistance(self, system2):
        return math.sqrt(self.calculateDistanceSquared(system2))

    def getNormalDistance(self):
        return math.sqrt(self.distanceSquared)

this = sys.modules[__name__]	# For holding module globals

def plugin_start():
    return 'EDSM-RSE'

def plugin_prefs(parent):
    frame = nb.Frame(parent)
    return frame

def plugin_app(parent):
    frame = tk.Frame(parent)
    return frame

def journal_entry(cmdr, system, station, entry, state):
    pass

