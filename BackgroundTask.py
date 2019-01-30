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
import urllib2
import time

from RseData import RseData

if __debug__:
    from traceback import print_exc


class BackgroundTask(object):
    def __init__(self, rseData):
        self.rseData = rseData

    def execute(self):
        pass  # to be implemented by subclass

    def getSystemFromID(self, id64):
        system = filter(lambda x: x.id == id64, self.rseData.systemList)[
                 :1]  # there is only one possible match for ID64, avoid exception being thrown
        if len(system) > 0:
            return system[0]
        else:
            return None

    def fireEvent(self):
        self.rseData.lastEventInfo.clear()
        if len(self.rseData.systemList) > 0:
            self.rseData.lastEventInfo[RseData.BG_SYSTEM] = self.rseData.systemList[0]
        else:
            self.rseData.lastEventInfo[RseData.BG_MESSAGE] = "No system in range"
        if self.rseData.frame:
            self.rseData.frame.event_generate(RseData.EVENT_RSE_BACKGROUNDWORKER, when="tail")  # calls updateUI in main thread

    def removeSystems(self):
        removeMe = filter(lambda x: x.action == 0, self.rseData.systemList)
        if __debug__: print(
            "adding {count} systems to removal filter: {systems}".format(count=len(removeMe), systems=[x.name for x in removeMe]))
        self.rseData.systemList = [x for x in self.rseData.systemList if x not in removeMe]
        self.rseData.openLocalDatabase()
        for system in removeMe:
            self.rseData.filter.add(system.id)
            self.rseData.addSystemToCache(system.id, time.time() + 24 * 3600, handleDbConnection=False)
        self.rseData.closeLocalDatabase()


class NavbeaconTask(BackgroundTask):
    def __init__(self, rseUtils, systemAddress):
        super(NavbeaconTask, self).__init__(rseUtils)
        self.systemAddress = systemAddress

    def execute(self):
        system = self.getSystemFromID(self.systemAddress)
        if system:
            system.removeFromProject(RseData.PROJECT_NAVBEACON)
            self.removeSystems()
            self.fireEvent()


class JumpedSystemTask(BackgroundTask):
    def __init__(self, rseUtils, coordinates, systemAddress):
        super(JumpedSystemTask, self).__init__(rseUtils)
        self.coordinates = coordinates
        self.systemAddress = systemAddress

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

    def execute(self):
        system = self.getSystemFromID(self.systemAddress)

        if system:  # arrived in system without coordinates
            if __debug__: print("arrived in {}".format(system.name))
            system.removeFromProject(RseData.PROJECT_RSE)
            self.removeSystems()

        if self.rseData.generateListsFromRemoteDatabase(*self.coordinates):
            lowerLimit = 0
            upperLimit = RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            tries = 0
            while tries < 3 and len(self.rseData.systemList) > 0:  # no do-while loops...
                closestSystems = self.rseData.systemList[lowerLimit:upperLimit]
                edsmResults = self.queryEDSM(closestSystems)
                if len(edsmResults) > 0:
                    # remove systems with coordinates
                    systemsWithCoordinates = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                    for system in systemsWithCoordinates:
                        system.removeFromProject(RseData.PROJECT_RSE)
                    self.removeSystems()
                    closestSystems = filter(lambda s: s.name.lower() in edsmResults, closestSystems)
                if len(closestSystems) > 0:
                    # there are still systems in the results -> stop here
                    break
                else:
                    tries += 1
                    lowerLimit += RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY
                    upperLimit += RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY

            self.fireEvent()

        else:
            # distances need to be recalculated because we couldn't get a new list from the database
            for system in self.rseData.systemList:
                system.updateDistanceToCurrentCommanderPosition(*self.coordinates)
            self.rseData.systemList.sort(key=lambda l: l.distance)


class IgnoreSystemTask(BackgroundTask):
    def __init__(self, rseData, systemName, duration=0):
        super(IgnoreSystemTask, self).__init__(rseData)
        self.systemName = systemName
        self.duration = duration

    def execute(self):
        for system in self.rseData.systemList:
            if system.name.lower() == self.systemName.lower():
                system.action = 0
                self.removeSystems()
                if self.duration > 0:
                    self.rseData.addSystemToCache(system.id, self.duration)
                self.fireEvent()
                break


class VersionCheckTask(BackgroundTask):
    def __init__(self, rseData):
        super(VersionCheckTask, self).__init__(rseData)

    def execute(self):
        try:
            request = urllib2.Request(RseData.VERSION_CHECK_URL)
            response = urllib2.urlopen(request)
            newVersionInfo = json.loads(response.read())
            if RseData.VERSION != newVersionInfo["version"]:
                self.rseData.lastEventInfo[RseData.BG_JSON] = newVersionInfo
                self.rseData.frame.event_generate(RseData.EVENT_RSE_UPDATE_AVAILABLE, when="tail")
        except ValueError:
            pass  # ignore


class TimedTask(BackgroundTask):
    # the reason this class exists is to use the task queue for the timer
    def __init__(self, rseData):
        super(TimedTask, self).__init__(rseData)

    def execute(self):
        self.rseData.removeExpiredSystemsFromCache()
