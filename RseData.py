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
import os
import time
import math
import sqlite3
import json
import logging
from config import appname

try:
    # Python 2
    from urllib2 import urlopen
    from urllib import urlencode
except ModuleNotFoundError:
    # Python 3
    from urllib.request import urlopen
    from urllib.parse import urlencode
    # from typing import Dict, List, Any, Set


logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")


class RseProject(object):
    def __init__(self, projectId, actionText, name, explanation, enabled):
        self.projectId = projectId  # type: int
        self.actionText = actionText  # type: str
        self.name = name
        self.explanation = explanation
        self.enabled = enabled  # type: int


class EliteSystem(object):
    def __init__(self, id64, name, x, y, z, uncertainty=0):
        self.id64 = id64
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.uncertainty = uncertainty  # type: int
        self.distance = 10000  # set initial value to be out of reach
        self.__rseProjects = dict()  # type: Dict[int, RseProject]

    @staticmethod
    def calculateDistance(x1, x2, y1, y2, z1, z2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def getCoordinates(self):
        return self.x, self.y, self.z

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distance = self.calculateDistanceToCoordinates(x, y, z)

    def calculateDistanceToCoordinates(self, x, y, z):
        return self.calculateDistance(self.x, x, self.y, y, self.z, z)

    def removeFromProject(self, projectId):
        if projectId in self.__rseProjects:
            del self.__rseProjects[projectId]

    def removeFromAllProjects(self):
        self.__rseProjects.clear()

    def addToProject(self, rseProject):
        self.__rseProjects.setdefault(rseProject.projectId, rseProject)

    def addToProjects(self, rseProjects):
        for rseProject in rseProjects:  # type: List[RseProject]
            self.addToProject(rseProject)

    def getProjectIds(self):
        return self.__rseProjects.keys()

    def calculateDistanceToSystem(self, system2):
        """
        Calculate distance to other EliteSystem
        :param system2: EliteSystem
        :return: distance as float
        """
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

    VERSION = "1.3.1"
    VERSION_CHECK_URL = "https://api.github.com/repos/Thurion/EDSM-RSE-for-EDMC/releases"
    PLUGIN_NAME = "EDSM-RSE"

    # settings for search radius
    DEFAULT_RADIUS_EXPONENT = 5  # key for radius, see calculateRadius
    MAX_RADIUS = 10
    RADIUS_ADJUSTMENT_INCREASE = 15  # increase radius if at most this amount of systems were found
    RADIUS_ADJUSTMENT_DECREASE = 100  # decrease the radius if at least this amount of systems were found

    EDSM_NUMBER_OF_SYSTEMS_TO_QUERY = 15

    # Values for projects
    PROJECT_RSE = 1
    PROJECT_NAVBEACON = 2
    PROJECT_SCAN = 4

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

    def __init__(self, pluginDir, radiusExponent=DEFAULT_RADIUS_EXPONENT):
        self.pluginDir = pluginDir  # type: str
        self.newVersionInfo = None
        self.systemList = list()  # type: List[EliteSystem] # nearby systems, sorted by distance
        self.projectsDict = dict()  # type: Dict[int, RseProject] # key = ID
        self.frame = None
        self.lastEventInfo = dict()  # type: Dict[str, Any] # used to pass values to UI. don't assign a new value! use clear() instead
        self.radiusExponent = radiusExponent  # type: int
        self.frame = None  # tk frame
        self.localDbCursor = None
        self.localDbConnection = None
        self.ignoredProjectsFlags = 0  # bit mask of ignored projects (AND of all their IDs)

        """ 
        Dictionary of sets that contain the cached systems. 
        Key for the dictionary is the value of one of the CACHE_<type> variables. The value is the set that holds the corresponding systems 
        Key for set is the ID64 of the cached system
        """
        self.__cachedSystems = dict()  # type: Dict[int, Set[int]]

    def getCachedSet(self, cacheType):
        """
        Return set of cached systems or empty set.
        :param cacheType: int
        :return:
        """
        if cacheType in self.__cachedSystems:
            return self.__cachedSystems.get(cacheType)
        else:
            return self.__cachedSystems.setdefault(cacheType, set())

    def setFrame(self, frame):
        self.frame = frame

    def openLocalDatabase(self):
        try:
            self.localDbConnection = sqlite3.connect(os.path.join(self.pluginDir, "cache.sqlite"), timeout=10)
            self.localDbCursor = self.localDbConnection.cursor()
        except Exception as e:
            errorMessage = "Local cache database could not be opened"
            logger.exception(errorMessage)
            plug.show_error(plug.show_error("{plugin_name}-{version}: {msg}".format(plugin_name=RseData.PLUGIN_NAME, version=RseData.VERSION, msg=errorMessage)))

    def closeLocalDatabase(self):
        if not self.isLocalDatabaseAccessible():
            return  # database not loaded
        self.localDbConnection.close()
        self.localDbCursor = None
        self.localDbConnection = None

    def isLocalDatabaseAccessible(self):
        return hasattr(self, "localDbCursor") and self.localDbCursor

    def adjustRadiusExponent(self):
        """
        Adjust the radius to ensure that not too many systems are found (decrease network traffic and database load)
        """

        def inverseCalculateRadius(d):
            if d > 50:
                return math.log((d - 39) / 11, 2)
            else:
                return 0

        numberOfSystems = len(self.systemList)

        # not enough systems in range
        if numberOfSystems <= RseData.RADIUS_ADJUSTMENT_INCREASE:
            self.radiusExponent = int(self.radiusExponent) + 1
            if self.radiusExponent > RseData.MAX_RADIUS:
                self.radiusExponent = 10
            logger.debug("Found too few systems, increasing radius to {1}.".format(numberOfSystems, self.calculateRadius()))

        # too many systems in range
        elif numberOfSystems >= RseData.RADIUS_ADJUSTMENT_DECREASE:
            self.radiusExponent = inverseCalculateRadius(self.systemList[RseData.RADIUS_ADJUSTMENT_DECREASE - 1].distance)
            if self.radiusExponent > RseData.MAX_RADIUS:  # prevent large radius after calculating on cached systems after switching a commander
                self.radiusExponent = 10
            logger.debug("Found too many systems, decreasing radius to {1}.".format(numberOfSystems, self.calculateRadius()))

        # number of systems within limits but distance exceeds set radius when using cached list -> increase radius
        elif numberOfSystems > 0 and self.calculateRadius() < self.systemList[0].distance:
            self.radiusExponent = inverseCalculateRadius(self.systemList[0].distance)
            if self.radiusExponent > RseData.MAX_RADIUS:  # prevent large radius after calculating on cached systems
                self.radiusExponent = 10

    def calculateRadius(self, exponent=0):
        if not exponent:
            exponent = self.radiusExponent
        return 39 + 11 * (2 ** exponent)

    def generateIgnoredActionsList(self):
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

    def _queryRseAPI(self, rseUrl):
        """
        Internal method which only calls the API and returns a JSON object or None.
        :param rseUrl:
        :return: JSON or None
        """
        try:
            url = urlopen(rseUrl, timeout=10)
            if url.getcode() != 200:
                # some error occurred
                logger.debug("Error calling RSE API. HTTP code: {code}.".format(code=url.getcode()))
                logger.debug("Tried to call {url}.".format(url=rseUrl))
                return None
            response = url.read()
            return json.loads(response)
        except Exception as e:
            # some error occurred
            logger.debug("Error calling RSE API.", exc_info=e)
            logger.debug("Tried to call {url}.".format(url=rseUrl))
            return None

    def generateListsFromRemoteDatabase(self, cmdr_x, cmdr_y, cmdr_z):
        """
        Takes coordinates of commander and queries the server for systems that are in range. It takes the current set radius and sets any newly found
        systems to self.systemList. Returns True if new systems were found and False if no new systems were found.

        :param cmdr_x: x coordinate of current position
        :param cmdr_y: y coordinate of current position
        :param cmdr_z: z coordinate of current position
        :return: True when new systems were found and False if not
        """
        enabledFlags = self.generateIgnoredActionsList()
        if len(enabledFlags) == 0:
            return False

        if len(enabledFlags) == 2 ** len(self.projectsDict.values()) - 1:  # all projects are enabled, no need to specify any
            flags = list()
        else:
            flags = list(enabledFlags)

        params = {"x": cmdr_x, "y": cmdr_y, "z": cmdr_z,
                  "radius": self.calculateRadius(),
                  "flags": flags}
        rseUrl = "https://cyberlord.de/rse/systems.py?" + urlencode(params)

        rseJson = self._queryRseAPI(rseUrl)  # use an extra method for unit testing purposes
        if not rseJson:
            return False

        systems = list()  # type: List[EliteSystem]
        scannedSystems = self.getCachedSet(RseData.CACHE_FULLY_SCANNED_BODIES)

        for _row in rseJson:
            rse_id64 = _row["id"]
            rse_name = _row["name"]
            rse_x = _row["x"]
            rse_y = _row["y"]
            rse_z = _row["z"]
            uncertainty = _row["uncertainty"]
            action = _row["action_todo"]

            distance = EliteSystem.calculateDistance(cmdr_x, rse_x, cmdr_y, rse_y, cmdr_z, rse_z)
            if distance <= self.calculateRadius():
                eliteSystem = EliteSystem(rse_id64, rse_name, rse_x, rse_y, rse_z, uncertainty)
                eliteSystem.addToProjects([rseProject for rseProject in self.projectsDict.values() if action & rseProject.projectId])
                eliteSystem.distance = distance

                # special case: project 4 (scan bodies)
                if RseData.PROJECT_SCAN in eliteSystem.getProjectIds() and eliteSystem.id64 in scannedSystems:
                    eliteSystem.removeFromProject(RseData.PROJECT_SCAN)

                if len(eliteSystem.getProjectIds()) > 0:
                    systems.append(eliteSystem)

        if len(systems) == 0:
            return False  # nothing new

        # filter out systems that have been completed or are ignored
        systems = list(filter(lambda system: system.id64 not in self.getCachedSet(RseData.CACHE_IGNORED_SYSTEMS), systems))
        systems.sort(key=lambda l: l.distance)

        self.systemList = systems
        logger.debug("Found {systems} systems within {radius} ly.".format(systems=len(systems), radius=self.calculateRadius()))

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

    def removeAllSystemsFromCache(self, cacheType, handleDbConnection=True):
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
        if len(self.projectsDict) == 0:
            response = self._queryRseAPI("https://cyberlord.de/rse/projects.py")
            if not response:
                errorMessage = "Could not get information about projects."
                logger.error(errorMessage)
                plug.show_error("{plugin_name}-{version}: {msg}".format(plugin_name=RseData.PLUGIN_NAME, version=RseData.VERSION, msg=errorMessage))
            else:
                for _row in response:
                    rseProject = RseProject(_row["id"], _row["action_text"], _row["project_name"], _row["explanation"], _row["enabled"])
                    self.projectsDict[rseProject.projectId] = rseProject
