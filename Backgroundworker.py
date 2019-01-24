"""
EDSM-RSE a plugin for EDMC
Copyright (C) 2018 Sebastian Bauer

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

import json
import urllib2
import psycopg2
import plug
import sys

from threading import Thread

from EliteSystem import EliteSystem

if __debug__:
    from traceback import print_exc


class BackgroundWorker(Thread):
    # instructions. don't use 0!
    JUMPED_SYSTEM = 1
    NAVBEACON = 2

    DEFAULT_RADIUS = 2  # key for radius, see calculateRadius
    MAX_RADIUS = 10
    RADIUS_ADJUSTMENT_INCREASE = 15  # increase radius if at most this amount of systems were found
    RADIUS_ADJUSTMENT_DECREASE = 100  # decrease the radius if at least this amount of systems were found

    EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 15

    # Values for projects
    PROJECT_RSE = 1
    PROJECT_NAVBEACON = 2

    # keys for dictionary that stores data from the background thread
    # stored in this.lastEventInfo
    BG_SYSTEM = "bg_system"
    BG_MESSAGE = "bg_message"

    def __init__(self, queue, lastEventInfo, radius=DEFAULT_RADIUS):
        Thread.__init__(self)
        self.queue = queue
        self.radius = radius
        self.systemList = list()  # nearby systems, sorted by distance
        self.projectsDict = dict()
        self.filter = set()  # systems that have been completed
        self.lastEventInfo = lastEventInfo  # used to pass values to UI. don't assign a new value! use clear() instead
        self.frame = None
        self.c = None
        self.conn = None

    def setFrame(self, frame):
        self.frame = frame

    def calculateRadius(self, value):
        return 39 + 11 * (2 ** value)

    def ignore(self, systemName):
        for system in self.systemList:
            if system.name.lower() == systemName.lower():
                system.action = 0
                self.removeSystems()
                self.showNewClosestSystem()
                break

    def adjustRadius(self, numberOfSystems):
        if numberOfSystems <= self.RADIUS_ADJUSTMENT_INCREASE:
            self.radius += 1
            if self.radius > self.MAX_RADIUS:
                self.radius = 10
            if __debug__: print("found {0} systems, increasing radius to {1}".format(numberOfSystems, self.calculateRadius(self.radius)))
        elif numberOfSystems >= self.RADIUS_ADJUSTMENT_DECREASE:
            self.radius -= 1
            if self.radius < 0:
                self.radius = 0
            if __debug__: print("found {0} systems, decreasing radius to {1}".format(numberOfSystems, self.calculateRadius(self.radius)))

    def openDatabase(self):
        try:
            self.conn = psycopg2.connect(host="cyberlord.de", port=5432, dbname="edmc_rse_db", user="edmc_rse_user",
                                         password="asdfplkjiouw3875948zksmdxnf", application_name="EDSM-RSE", connect_timeout=10)
            self.c = self.conn.cursor()
        except Exception as e:
            plug.show_error("EDSM-RSE: Database could not be opened")
            sys.stderr.write("EDSM-RSE: Database could not be opened\n")

    def closeDatabase(self):
        if not hasattr(self, "c") or not self.c:
            return  # database not loaded
        self.conn.close()
        self.c = None
        self.conn = None

    def initializeDictionaries(self):
        if not hasattr(self, "c") or not self.c:
            return  # database not loaded

        if len(self.projectsDict) == 0:
            self.c.execute("SELECT id,action_text FROM projects")
            self.projectsDict = dict()
            for _row in self.c.fetchall():
                _id, action_text = _row
                self.projectsDict[_id] = action_text

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
            "x1": x - self.calculateRadius(self.radius),
            "x2": x + self.calculateRadius(self.radius),
            "y1": y - self.calculateRadius(self.radius),
            "y2": y + self.calculateRadius(self.radius),
            "z1": z - self.calculateRadius(self.radius),
            "z2": z + self.calculateRadius(self.radius)
        })
        for _row in self.c.fetchall():
            _, name, x2, y2, z2, uncertainty, action = _row
            distance = EliteSystem.calculateDistance(x, x2, y, y2, z, z2)
            if distance <= self.calculateRadius(self.radius):
                eliteSystem = EliteSystem(*_row)
                eliteSystem.distance = distance
                eliteSystem.action_text = ", ".join(
                    [self.projectsDict[project] for project in self.projectsDict.keys() if (eliteSystem.action & project) == project])
                systems.append(eliteSystem)

        # filter out systems that have been completed
        systems = filter(lambda system: system.id not in self.filter, systems)
        systems.sort(key=lambda l: l.distance)

        self.systemList = systems
        self.adjustRadius(len(self.systemList))

    def removeSystems(self):
        removeMe = filter(lambda x: x.action == 0, self.systemList)
        if __debug__: print(
            "adding {count} systems to removal filter: {systems}".format(count=len(removeMe), systems=[x.name for x in removeMe]))
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

    def getSystemFromID(self, id64):
        system = filter(lambda x: x.id == id64, self.systemList)[
                 :1]  # there is only one possible match for ID64, avoid exception being thrown
        if len(system) > 0:
            return system[0]
        else:
            return None

    def showNewClosestSystem(self):
        self.lastEventInfo.clear()
        if len(self.systemList) > 0:
            self.lastEventInfo[self.BG_SYSTEM] = self.systemList[0]
        else:
            self.lastEventInfo[self.BG_MESSAGE] = "No system in range"
        if self.frame:
            self.frame.event_generate("<<EDSM-RSE_BackgroundWorker>>", when="tail")  # calls updateUI in main thread

    def handleJumpedSystem(self, coordinates, systemAddress):
        system = self.getSystemFromID(systemAddress)

        if system:  # arrived in system without coordinates
            if __debug__: print("arrived in {}".format(system.name))
            system.removeFromProject(self.PROJECT_RSE)
            self.removeSystems()

        if hasattr(self, "c") and self.c:  # make sure the database is accessible
            self.generateListsFromDatabase(*coordinates)
            lowerLimit = 0
            upperLimit = self.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            tries = 0
            while tries < 3 and len(self.systemList) > 0:  # no do-while loops...
                closestSystems = self.systemList[lowerLimit:upperLimit]
                edsmResults = self.queryEDSM(closestSystems)
                if len(edsmResults) > 0:
                    # remove systems with coordinates
                    systemsWithCoordinates = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                    for system in systemsWithCoordinates:
                        system.removeFromProject(self.PROJECT_RSE)
                    self.removeSystems()
                    closestSystems = filter(lambda s: s.name.lower() in edsmResults, closestSystems)
                if len(closestSystems) > 0:
                    # there are still systems in the results -> stop here
                    break
                else:
                    tries += 1
                    lowerLimit += self.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
                    upperLimit += self.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            self.showNewClosestSystem()

        else:
            # distances need to be recalculated because we couldn't get a new list from the database
            for system in self.systemList:
                system.updateDistanceToCurrentCommanderPosition(*coordinates)
            self.systemList.sort(key=lambda l: l.distance)

    def handleNavbeacon(self, systemAddress):
        system = self.getSystemFromID(systemAddress)
        if system:
            system.removeFromProject(self.PROJECT_NAVBEACON)
            self.removeSystems()
            self.showNewClosestSystem()

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
                self.handleNavbeacon(args)  # args is only 1 ID64
            self.queue.task_done()
        self.closeDatabase()
        self.queue.task_done()
