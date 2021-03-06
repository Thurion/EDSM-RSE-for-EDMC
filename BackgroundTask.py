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

import json
import time
import math
import logging
import os
from RseData import RseData
from config import appname

try:
    # Python 2
    from urllib2 import quote
    from urllib2 import urlopen
except ModuleNotFoundError:
    # Python 3
    from urllib.parse import quote
    from urllib.request import urlopen, Request

logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")


class BackgroundTask(object):
    """
    Template for new tasks.
    """
    def __init__(self, rseData):
        self.rseData = rseData

    def execute(self):
        logger.critical("{} Didn't implement execute.".format(self.__class__.__name__))
        pass  # to be implemented by subclass

    def fireEvent(self):
        logger.critical("{} Didn't implement fireEvent.".format(self.__class__.__name__))
        pass  # to be implemented by subclass


class BackgroundTaskClosestSystem(BackgroundTask):
    def __init__(self, rseData):
        super(BackgroundTaskClosestSystem, self).__init__(rseData)

    def fireEvent(self):
        if len(self.rseData.systemList) > 0:
            self.rseData.lastEventInfo[RseData.BG_RSE_SYSTEM] = self.rseData.systemList[0]
        else:
            self.rseData.lastEventInfo[RseData.BG_RSE_SYSTEM] = None
            self.rseData.lastEventInfo[RseData.BG_RSE_MESSAGE] = "No system in range"
        if self.rseData.frame:
            self.rseData.frame.event_generate(RseData.EVENT_RSE_BACKGROUNDWORKER, when="tail")  # calls updateUI in main thread

    def getSystemFromID(self, id64):
        system = list(filter(lambda x: x.id64 == id64, self.rseData.systemList))  # there is only one possible match for ID64, avoid exception being thrown
        if len(system) > 0:
            return system[0]
        else:
            return None

    def removeSystems(self):
        removeMe = list(filter(lambda x: len(x.getProjectIds()) == 0, self.rseData.systemList))
        logger.debug("Adding {count} systems to removal filter: {systems}.".format(count=len(removeMe), systems=[x.name for x in removeMe]))
        self.rseData.systemList = [x for x in self.rseData.systemList if x not in removeMe]
        self.rseData.openLocalDatabase()
        for system in removeMe:
            self.rseData.getCachedSet(RseData.CACHE_IGNORED_SYSTEMS).add(system.id64)
            self.rseData.addSystemToCache(system.id64, time.time() + 24 * 3600, RseData.CACHE_IGNORED_SYSTEMS, handleDbConnection=False)
        self.rseData.closeLocalDatabase()


class NavbeaconTask(BackgroundTaskClosestSystem):
    def __init__(self, rseData, systemAddress):
        super(NavbeaconTask, self).__init__(rseData)
        self.systemAddress = systemAddress

    def execute(self):
        system = self.getSystemFromID(self.systemAddress)
        if system:
            system.removeFromProject(RseData.PROJECT_NAVBEACON)
            self.removeSystems()
            self.fireEvent()


class JumpedSystemTask(BackgroundTaskClosestSystem):
    def __init__(self, rseData, eliteSystem):
        super(JumpedSystemTask, self).__init__(rseData)
        self.coordinates = eliteSystem.getCoordinates()
        self.systemAddress = eliteSystem.id64

    def queryEDSM(self, systems):
        """ returns a set of systems names in lower case with unknown coordinates """
        edsmUrl = "https://www.edsm.net/api-v1/systems?onlyUnknownCoordinates=1&"
        params = list()
        names = set()
        cache = self.rseData.getCachedSet(RseData.CACHE_EDSM_RSE_QUERY)  # type: set
        cache = cache.union(self.rseData.getCachedSet(RseData.CACHE_IGNORED_SYSTEMS))
        addToCache = list()
        for system in systems:
            if system.uncertainty > 0:
                if system.id64 not in cache:
                    params.append("systemName[]={name}".format(name=quote(system.name)))
                    addToCache.append(system.id64)
                else:
                    names.add(system.name.lower())  # name is in EDSM cache -> make sure the name is returned as if included in EDSM call
        edsmUrl += "&".join(params)

        logger.debug("Querying EDSM for {} systems.".format(len(params)))

        if len(params) > 0:
            try:
                url = urlopen(edsmUrl, timeout=10)
                response = url.read()
                edsmJson = json.loads(response)
                for entry in edsmJson:
                    names.add(entry["name"].lower())

                expirationTime = time.time() + 30 * 60  # ignore for 30 minutes
                self.rseData.openLocalDatabase()
                for id64 in addToCache:
                    self.rseData.addSystemToCache(id64, expirationTime, RseData.CACHE_EDSM_RSE_QUERY, handleDbConnection=False)
                self.rseData.closeLocalDatabase()

                return names
            except Exception as e:
                # ignore. the EDSM call is not required
                logger.debug("EDSM call failed.", exc_info=e)

        # something went wrong. Return all systems as unknown
        names = set()
        for system in systems:
            names.add(system.name.lower())
        return names

    def execute(self):
        system = self.getSystemFromID(self.systemAddress)

        if system:  # arrived in system without coordinates
            logger.debug("Arrived in {}.".format(system.name))
            system.removeFromProject(RseData.PROJECT_RSE)
            self.removeSystems()

        if not self.rseData.generateListsFromRemoteDatabase(*self.coordinates):
            # distances need to be recalculated because we couldn't get a new list from the database
            logger.debug("Using cached system list for targets. Radius was set to {}.".format(self.rseData.calculateRadius()))
            for system in self.rseData.systemList:
                system.updateDistanceToCurrentCommanderPosition(*self.coordinates)
            self.rseData.systemList.sort(key=lambda l: l.distance)
        self.rseData.adjustRadiusExponent()

        tries = 0
        while tries < 3 and len(self.rseData.systemList) > 0:  # no do-while loops...
            closestSystems = self.rseData.systemList[0:RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY]
            edsmResults = self.queryEDSM(closestSystems)
            if len(edsmResults) < RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY:
                # remove systems with coordinates
                systemsWithCoordinates = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                for system in systemsWithCoordinates:
                    system.removeFromProject(RseData.PROJECT_RSE)
                self.removeSystems()
            if len(edsmResults) > 0:
                # there are still systems in the results -> stop here
                break
            tries += 1

        self.fireEvent()


class IgnoreSystemTask(BackgroundTaskClosestSystem):
    def __init__(self, rseData, systemName, duration=0):
        super(IgnoreSystemTask, self).__init__(rseData)
        self.systemName = systemName
        self.duration = duration

    def execute(self):
        for system in self.rseData.systemList:
            if system.name.lower() == self.systemName.lower():
                self.rseData.getCachedSet(RseData.CACHE_IGNORED_SYSTEMS).add(system.id64)
                self.rseData.systemList.remove(system)
                if self.duration > 0:
                    self.rseData.addSystemToCache(system.id64, self.duration, RseData.CACHE_IGNORED_SYSTEMS)
                self.fireEvent()
                break


class VersionCheckTask(BackgroundTask):
    def __init__(self, rseData):
        super(VersionCheckTask, self).__init__(rseData)

    def execute(self):
        try:
            response = urlopen(RseData.VERSION_CHECK_URL, timeout=10)
            releasesInfo = json.loads(response.read())
            runningVersion = tuple(RseData.VERSION.split("."))
            for releaseInfo in releasesInfo:
                if not releaseInfo["draft"] and not releaseInfo["prerelease"]:
                    newVersionText = releaseInfo["tag_name"].split("_")[1]
                    newVersionInfo = tuple(newVersionText.split("."))
                    if runningVersion < newVersionInfo:
                        self.rseData.lastEventInfo[RseData.BG_UPDATE_JSON] = {"version": newVersionText, "url": releaseInfo["html_url"]}
                        self.rseData.frame.event_generate(RseData.EVENT_RSE_UPDATE_AVAILABLE, when="tail")
                        break
        except Exception as e:
            logger.exception("Failed to retrieve information about available updates.")


class TimedTask(BackgroundTask):
    # the reason this class exists is to use the task queue for the timer
    def __init__(self, rseData):
        super(TimedTask, self).__init__(rseData)

    def execute(self):
        self.rseData.removeExpiredSystemsFromCaches()


class DeleteSystemsFromCacheTask(BackgroundTask):
    def __init__(self, rseData, cacheType):
        super(DeleteSystemsFromCacheTask, self).__init__(rseData)
        self.cacheType = cacheType

    def execute(self):
        self.rseData.removeAllSystemsFromCache(self.cacheType)


class EdsmBodyCheck(BackgroundTaskClosestSystem):
    def __init__(self, rseData):
        super(EdsmBodyCheck, self).__init__(rseData)

    def fireEventEdsmBodyCheck(self, message=None):
        self.rseData.lastEventInfo[RseData.BG_EDSM_BODY] = message or "?"
        if self.rseData.frame:
            self.rseData.frame.event_generate(RseData.EVENT_RSE_EDSM_BODY_COUNT, when="tail")  # calls updateUI in main thread


class FSSAllBodiesFoundTask(EdsmBodyCheck):
    def __init__(self, rseData, id64, edsmBodyCheck):
        super(FSSAllBodiesFoundTask, self).__init__(rseData)
        self.id64 = id64
        self.edsmBodyCheck = edsmBodyCheck

    def execute(self):
        system = self.getSystemFromID(self.id64)
        if system:
            system.removeFromProject(RseData.PROJECT_SCAN)
            self.removeSystems()
            self.fireEvent()

        if self.edsmBodyCheck:
            self.rseData.addSystemToCache(self.id64, 2 ** 31 - 1, RseData.CACHE_FULLY_SCANNED_BODIES)  # overwrites entry in DB if it was set before
            self.fireEventEdsmBodyCheck("System complete")


class FSSDiscoveryScanTask(EdsmBodyCheck):
    def __init__(self, rseData, systemName, bodyCount, progress):
        super(FSSDiscoveryScanTask, self).__init__(rseData)
        self.systemName = systemName
        self.bodyCount = bodyCount
        self.progress = progress

    def queryEdsm(self):
        edsmUrl = "https://www.edsm.net/api-system-v1/bodies?systemName={name}".format(name=quote(self.systemName))
        logger.debug("Querying EDSM for bodies of system {}.".format(self.systemName))
        try:
            url = urlopen(edsmUrl, timeout=10)
            response = url.read()
            edsmJson = json.loads(response)
            return edsmJson["id64"], len(edsmJson["bodies"])
        except Exception as e:
            logger.debug("EDSM body count call failed.", exc_info=e)
        return None, None  # error/timeout occurred

    def execute(self):
        if self.progress == 1.0:
            self.fireEventEdsmBodyCheck("System complete")
            # no need to call EDSM's API here because all bodies are found and will be submitted to EDSM
            return

        id64, knownToEdsm = self.queryEdsm()
        if id64:
            if self.bodyCount == knownToEdsm:
                self.rseData.addSystemToCache(id64, int(math.pow(2, 31)) - 1, RseData.CACHE_FULLY_SCANNED_BODIES)
            self.fireEventEdsmBodyCheck("{onEDSM}/{inSystem}".format(inSystem=self.bodyCount, onEDSM=knownToEdsm))
        else:
            self.fireEventEdsmBodyCheck("{onEDSM}/{inSystem}".format(inSystem=self.bodyCount, onEDSM="?"))
