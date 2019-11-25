"""
EDSM-RSE a plugin for EDMC
Copyright (C) 2019 Sebastian Bauer

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
import plug
import sys
import os
import time
import math
import sqlite3
import psycopg2
from typing import Dict, List, Any, Set


class RseProject(object):
    def __init__(self, projectId: int, actionText: str, name: str, explanation: str, enabled: int):
        self.projectId = projectId
        self.actionText = actionText
        self.name = name
        self.explanation = explanation
        self.enabled = enabled


class EliteSystem(object):
    def __init__(self, id64: int, name: str, x, y, z, uncertainty: int = 0):
        self.id64 = id64
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.uncertainty = uncertainty
        self.distance = 10000  # set initial value to be out of reach
        self.__rseProjects = dict()  # type: Dict[int, RseProject]

    @staticmethod
    def calculateDistance(x1, x2, y1, y2, z1, z2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distance = self.calculateDistanceToCoordinates(x, y, z)

    def calculateDistanceToCoordinates(self, x2, y2, z2):
        return self.calculateDistance(self.x, x2, self.y, y2, self.z, z2)

    def removeFromProject(self, projectId: int):
        if projectId in self.__rseProjects:
            del self.__rseProjects[projectId]

    def removeFromAllProjects(self):
        self.__rseProjects.clear()

    def addToProject(self, rseProject: RseProject):
        self.__rseProjects.setdefault(rseProject.projectId, rseProject)

    def addToProjects(self, rseProjects: List[RseProject]):
        for rseProject in rseProjects:
            self.addToProject(rseProject)

    def getProjectIds(self):
        return self.__rseProjects.keys()

    def calculateDistanceToSystem(self, system2: "EliteSystem"):
        return self.calculateDistanceToCoordinates(system2.x, system2.y, system2.z)

    def getActionText(self):
        if len(self.__rseProjects) > 0:
            return ", ".join([rseProject.actionText for rseProject in self.__rseProjects.values()])
        else:
            return ""

    def __str__(self):
        return "id64: {id64}, name: {name}, distance: {distance:,.2f}, uncertainty: {uncertainty}"\
            .format(id64=self.id64, name=self.name, distance=self.distance, uncertainty=self.uncertainty)

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id64 == other.id64
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id64)


class RseData(object):

    VERSION = "1.3"
    VERSION_CHECK_URL = "https://gist.githubusercontent.com/Thurion/35553c9562297162a86722a28c7565ab/raw/RSE_update_info"

    # settings for search radius
    DEFAULT_RADIUS_EXPONENT = 2  # key for radius, see calculateRadius
    MAX_RADIUS = 10
    RADIUS_ADJUSTMENT_INCREASE = 15  # increase radius if at most this amount of systems were found
    RADIUS_ADJUSTMENT_DECREASE = 100  # decrease the radius if at least this amount of systems were found

    EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 15

    # Values for projects
    PROJECT_RSE = 1
    PROJECT_NAVBEACON = 2

    # keys for dictionary that stores data from the background thread
    BG_RSE_SYSTEM = "bg_rse_system"  # RSE system as string
    BG_RSE_MESSAGE = "bg_rse_message"  # RSE message as string
    BG_UPDATE_JSON = "bg_update_json"  # information about available update
    BG_EDSM_BODY = "bg_edsm_body"  # EDSM body count information as string

    # name of events
    EVENT_RSE_UPDATE_AVAILABLE = "<<EDSM-RSE_UpdateAvailable>>"
    EVENT_RSE_BACKGROUNDWORKER = "<<EDSM-RSE_BackgroundWorker>>"
    EVENT_RSE_EDSM_BODY_COUNT = "<<EDSM-RSE_EdsmBodyCount>>"

    # possible caches
    CACHE_IGNORED_SYSTEMS = 1
    CACHE_FULLY_SCANNED_BODIES = 2
    CACHE_EDSM_RSE_QUERY = 3

    def __init__(self, pluginDir: str, radiusExponent: int = DEFAULT_RADIUS_EXPONENT):
        self.pluginDir = pluginDir
        self.newVersionInfo = None
        self.systemList = list()  # type: List[EliteSystem] # nearby systems, sorted by distance
        self.projectsDict = dict()  # type: Dict[int, RseProject] # key = ID
        self.frame = None
        self.lastEventInfo = dict()  # type: Dict[str, Any] # used to pass values to UI. don't assign a new value! use clear() instead
        self.radiusExponent = radiusExponent
        self.frame = None  # tk frame
        self.remoteDbCursor = None
        self.remoteDbConnection = None
        self.localDbCursor = None
        self.localDbConnection = None
        self.ignoredProjectsFlags = 0  # bit mask of ignored projects (AND of all their IDs)

        """ 
        Dictionary of sets that contain the cached systems. 
        Key for the dictionary is the value of one of the CACHE_<type> variables. The value is the set that holds the corresponding systems 
        Key for set is the ID64 of the cached system
        """
        self.__cachedSystems = dict()  # type: Dict[int, Set[int]]

    def getCachedSet(self, cacheType: int) -> Set[int]:
        """ Return set of cached systems or empty set. """
        if cacheType in self.__cachedSystems:
            return self.__cachedSystems.get(cacheType)
        else:
            return self.__cachedSystems.setdefault(cacheType, set())

    def setFrame(self, frame):
        self.frame = frame

    def openRemoteDatabase(self):
        try:
            self.remoteDbConnection = psycopg2.connect(host="cyberlord.de", port=5432, dbname="edmc_rse_db", user="edmc_rse_user",
                                                       password="asdfplkjiouw3875948zksmdxnf", application_name="EDSM-RSE {}".format(RseData.VERSION), connect_timeout=10)
            self.remoteDbCursor = self.remoteDbConnection.cursor()
        except Exception as e:
            if __debug__:
                print("Remote database could not be opened")
            plug.show_error("EDSM-RSE: Remote database could not be opened")
            sys.stderr.write("EDSM-RSE: Remote database could not be opened\n")

    def closeRemoteDatabase(self):
        if not self.isRemoteDatabaseAccessible():
            return  # database not loaded
        self.remoteDbConnection.close()
        self.remoteDbCursor = None
        self.remoteDbConnection = None

    def isRemoteDatabaseAccessible(self):
        return hasattr(self, "remoteDbCursor") and self.remoteDbCursor

    def openLocalDatabase(self):
        try:
            self.localDbConnection = sqlite3.connect(os.path.join(self.pluginDir, "cache.sqlite"))
            self.localDbCursor = self.localDbConnection.cursor()
        except Exception as e:
            if __debug__:
                print("Local cache database could not be opened")
            plug.show_error("EDSM-RSE: Local cache database could not be opened")
            sys.stderr.write("EDSM-RSE: Local cache database could not be opened\n")

    def closeLocalDatabase(self):
        if not self.isLocalDatabaseAccessible():
            return  # database not loaded
        self.localDbConnection.close()
        self.localDbCursor = None
        self.localDbConnection = None

    def isLocalDatabaseAccessible(self):
        return hasattr(self, "localDbCursor") and self.localDbCursor

    def adjustRadiusExponent(self, numberOfSystems: int):
        """
        Adjust the radius to ensure that not too many systems are found (decrease network traffic and database load)
        :param numberOfSystems:  number of systems found the last time
        """
        if numberOfSystems <= RseData.RADIUS_ADJUSTMENT_INCREASE:
            self.radiusExponent += 1
            if self.radiusExponent > RseData.MAX_RADIUS:
                self.radiusExponent = 10
            if __debug__: print("found {0} systems, increasing radius to {1}".format(numberOfSystems, self.calculateRadius()))
        elif numberOfSystems >= RseData.RADIUS_ADJUSTMENT_DECREASE:
            self.radiusExponent -= 1
            if self.radiusExponent < 0:
                self.radiusExponent = 0
            if __debug__: print("found {0} systems, decreasing radius to {1}".format(numberOfSystems, self.calculateRadius()))

    def calculateRadius(self):
        return 39 + 11 * (2 ** self.radiusExponent)

    def generateIgnoredActionsList(self) -> Set[int]:
        """
        TODO
        currently it ignores all systems that are part of a project. lets say we have a system that is part of 2 projects
        and the user ignores one of them. then it won't be in the list
        might want to change that and just remove the project from the local action flag
        """
        enabledFlags = set()
        combinedIgnoredFlags = self.ignoredProjectsFlags

        for rseProject in self.projectsDict.values():
            if not rseProject.enabled:
                combinedIgnoredFlags = combinedIgnoredFlags | rseProject.projectId
        for i in range(1, (2 ** len(self.projectsDict.values()))):  # generate all possible bit masks
            flag = i & ~combinedIgnoredFlags
            if flag > 0:
                enabledFlags.add(flag)
        return enabledFlags

    def generateListsFromRemoteDatabase(self, x, y, z, handleDbConnection=True):
        if handleDbConnection:
            self.openRemoteDatabase()

        enabledFlags = self.generateIgnoredActionsList()
        if not self.isRemoteDatabaseAccessible() or len(enabledFlags) == 0:
            return False

        queryDictionary = {
            "x1": x - self.calculateRadius(),
            "x2": x + self.calculateRadius(),
            "y1": y - self.calculateRadius(),
            "y2": y + self.calculateRadius(),
            "z1": z - self.calculateRadius(),
            "z2": z + self.calculateRadius()}

        if len(enabledFlags) == 2 ** len(self.projectsDict.values()):  # all projects are enabled
            whereCondition = "deleted_at IS NULL;"
        else:
            whereCondition = "deleted_at IS NULL AND action_todo = ANY(%(flags)s);"
            queryDictionary.setdefault("flags", list(enabledFlags))

        sql = " ".join([
            "SELECT id, name, x, y, z, uncertainty, action_todo FROM systems WHERE",
            "systems.x BETWEEN %(x1)s AND %(x2)s AND",
            "systems.y BETWEEN %(y1)s AND %(y2)s AND",
            "systems.z BETWEEN %(z1)s AND %(z2)s AND",
            whereCondition
        ])
        systems = list()
        # make sure that the between statements are BETWEEN lower limit AND higher limit
        self.remoteDbCursor.execute(sql, queryDictionary)
        for _row in self.remoteDbCursor.fetchall():
            id64, name, x2, y2, z2, uncertainty, action = _row
            distance = EliteSystem.calculateDistance(x, x2, y, y2, z, z2)
            if distance <= self.calculateRadius():
                eliteSystem = EliteSystem(id64, name, x, y, z, uncertainty)
                eliteSystem.addToProjects([rseProject for rseProject in self.projectsDict.values() if action & rseProject.projectId])
                eliteSystem.distance = distance
                systems.append(eliteSystem)

        # filter out systems that have been completed or are ignored
        systems = list(filter(lambda system: system.id64 not in self.getCachedSet(RseData.CACHE_IGNORED_SYSTEMS), systems))
        systems.sort(key=lambda l: l.distance)

        self.systemList = systems

        if handleDbConnection:
            self.closeRemoteDatabase()

        return True

    def removeExpiredSystemsFromCaches(self, handleDbConnection=True):
        if handleDbConnection:
            self.openLocalDatabase()
        if not self.isLocalDatabaseAccessible():
            return  # can't do anything here

        now = time.time()
        self.localDbCursor.execute("SELECT id64, cacheType FROM CachedSystems WHERE expirationDate <= ?", (now,))
        for row in self.localDbCursor.fetchall():
            id64, cacheType = row
            cache = self.getCachedSet(cacheType)
            if id64 in cache:
                cache.remove(id64)
        self.localDbCursor.execute("DELETE FROM CachedSystems WHERE expirationDate <= ?", (now,))
        self.localDbConnection.commit()

        if handleDbConnection:
            self.closeLocalDatabase()

    def removeAllSystemsFromCache(self, cacheType: int, handleDbConnection=True):
        if handleDbConnection:
            self.openLocalDatabase()
        if not self.isLocalDatabaseAccessible():
            return  # no database connection

        self.localDbCursor.execute("DELETE FROM CachedSystems WHERE id64 NOT NULL AND cacheType = ?", (cacheType,))
        self.localDbConnection.commit()

        if handleDbConnection:
            self.closeLocalDatabase()

    def addSystemToCache(self, id64, expirationTime, cacheType, handleDbConnection=True):
        if handleDbConnection:
            self.openLocalDatabase()
        if self.isLocalDatabaseAccessible():
            self.localDbCursor.execute("INSERT OR REPLACE INTO CachedSystems VALUES (?, ?, ?)", (id64, expirationTime, cacheType))
            self.localDbConnection.commit()
        if handleDbConnection:
            self.closeLocalDatabase()

    def initialize(self):
        # initialize local cache
        self.openLocalDatabase()
        if self.isLocalDatabaseAccessible():
            self.localDbCursor.execute("""CREATE TABLE IF NOT EXISTS `CachedSystems` (
                                            `id64`	          INTEGER,
                                            `expirationDate`  REAL NOT NULL,
                                            `cacheType`	      INTEGER NOT NULL,
                                            PRIMARY KEY(`id64`));""")
            self.localDbConnection.commit()
            self.removeExpiredSystemsFromCaches(handleDbConnection=False)

            # read cached systems
            self.localDbCursor.execute("SELECT id64, cacheType FROM CachedSystems")
            for row in self.localDbCursor.fetchall():
                id64, cacheType = row
                self.getCachedSet(cacheType).add(id64)
            self.closeLocalDatabase()

        # initialize dictionaries
        self.openRemoteDatabase()
        if self.isRemoteDatabaseAccessible():
            if len(self.projectsDict) == 0:
                self.remoteDbCursor.execute("SELECT id, action_text, project_name, explanation, enabled FROM projects")
                for _row in self.remoteDbCursor.fetchall():
                    rseProject = RseProject(*_row)
                    self.projectsDict[rseProject.projectId] = rseProject
            self.closeRemoteDatabase()
