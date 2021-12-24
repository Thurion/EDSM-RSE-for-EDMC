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
import requests

from urllib.parse import quote
from typing import Dict, Set

from RseData import RseData, EliteSystem
from config import appname

logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")


class BackgroundTask(object):
    """
    Template for new tasks.
    """
    def __init__(self, rse_data: RseData):
        self.rse_data = rse_data

    def execute(self):
        logger.critical(f"{self.__class__.__name__} Didn't implement execute.")
        pass  # to be implemented by subclass

    def fire_event(self):
        logger.critical(f"{self.__class__.__name__} Didn't implement fireEvent.")
        pass  # to be implemented by subclass


class BackgroundTaskClosestSystem(BackgroundTask):
    def __init__(self, rse_data):
        super(BackgroundTaskClosestSystem, self).__init__(rse_data)

    def fire_event(self):
        if len(self.rse_data.system_list) > 0:
            self.rse_data.last_event_info[RseData.BG_RSE_SYSTEM] = self.rse_data.system_list[0]
        else:
            self.rse_data.last_event_info[RseData.BG_RSE_SYSTEM] = None
            self.rse_data.last_event_info[RseData.BG_RSE_MESSAGE] = "No system in range"
        if self.rse_data.frame:
            self.rse_data.frame.event_generate(RseData.EVENT_RSE_BACKGROUNDWORKER, when="tail")  # calls updateUI in main thread

    def get_system_from_id(self, id64):
        system = list(filter(lambda x: x.id64 == id64, self.rse_data.system_list))  # there is only one possible match for ID64, avoid exception being thrown
        if len(system) > 0:
            return system[0]
        else:
            return None

    def remove_systems(self):
        remove_me = list(filter(lambda x: len(x.get_project_ids()) == 0, self.rse_data.system_list))
        logger.debug(f"Adding {len(remove_me)} systems to removal filter: {[x.name for x in remove_me]}.")
        self.rse_data.system_list = [x for x in self.rse_data.system_list if x not in remove_me]
        self.rse_data.open_local_database()
        for system in remove_me:
            self.rse_data.get_cached_set(RseData.CACHE_IGNORED_SYSTEMS).add(system.id64)
            self.rse_data.add_system_to_cache(system.id64, int(time.time() + 24 * 3600),
                                              RseData.CACHE_IGNORED_SYSTEMS, handle_db_connection=False)
        self.rse_data.close_local_database()


class NavbeaconTask(BackgroundTaskClosestSystem):
    def __init__(self, rse_data: RseData, system_address: Dict[str, str]):
        super(NavbeaconTask, self).__init__(rse_data)
        self.system_address = system_address

    def execute(self):
        system = self.get_system_from_id(self.system_address)
        if system:
            system.remove_from_project(RseData.PROJECT_NAVBEACON)
            self.remove_systems()
            self.fire_event()


class JumpedSystemTask(BackgroundTaskClosestSystem):
    def __init__(self, rse_data: RseData, elite_system: EliteSystem):
        super(JumpedSystemTask, self).__init__(rse_data)
        self.coordinates = elite_system.get_coordinates()
        self.system_address = elite_system.id64

    def query_edsm(self, systems) -> Set[str]:
        """ returns a set of systems names in lower case with unknown coordinates """
        edsm_url = "https://www.edsm.net/api-v1/systems?onlyUnknownCoordinates=1&"
        params = list()
        names = set()
        cache = self.rse_data.get_cached_set(RseData.CACHE_EDSM_RSE_QUERY)  # type: set
        cache = cache.union(self.rse_data.get_cached_set(RseData.CACHE_IGNORED_SYSTEMS))
        add_to_cache = list()
        for system in systems:
            if system.uncertainty > 0:
                if system.id64 not in cache:
                    params.append(f"systemName[]={quote(system.name)}")
                    add_to_cache.append(system.id64)
                else:
                    names.add(system.name.lower())  # name is in EDSM cache -> make sure the name is returned as if included in EDSM call
        edsm_url += "&".join(params)

        logger.debug(f"Querying EDSM for {len(params)} systems.")

        if len(params) > 0:
            try:
                response = requests.get(edsm_url, timeout=10)
                edsm_json = json.loads(response.text)
                for entry in edsm_json:
                    names.add(entry["name"].lower())

                expiration_time = int(time.time() + 30 * 60)  # ignore for 30 minutes
                self.rse_data.open_local_database()
                for id64 in add_to_cache:
                    self.rse_data.add_system_to_cache(id64, expiration_time, RseData.CACHE_EDSM_RSE_QUERY, handle_db_connection=False)
                self.rse_data.close_local_database()

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
        system = self.get_system_from_id(self.system_address)

        if system:  # arrived in system without coordinates
            logger.debug(f"Arrived in {system.name}.")
            system.remove_from_project(RseData.PROJECT_RSE)
            self.remove_systems()

        if not self.rse_data.generate_lists_from_remote_database(*self.coordinates):
            # distances need to be recalculated because we couldn't get a new list from the database
            logger.debug(f"Using cached system list for targets. Radius was set to {self.rse_data.calculate_radius()}.")
            for system in self.rse_data.system_list:
                system.update_distance_to_current_commander_position(*self.coordinates)
            self.rse_data.system_list.sort(key=lambda l: l.distance)
        self.rse_data.adjust_radius_exponent()

        tries = 0
        while tries < 3 and len(self.rse_data.system_list) > 0:  # no do-while loops...
            closestSystems = self.rse_data.system_list[0:RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY]
            edsmResults = self.query_edsm(closestSystems)
            if len(edsmResults) < RseData.EDSM_NUMBER_OF_SYSTEMS_TO_QUERY:
                # remove systems with coordinates
                systemsWithCoordinates = filter(lambda s: s.name.lower() not in edsmResults, closestSystems)
                for system in systemsWithCoordinates:
                    system.remove_from_project(RseData.PROJECT_RSE)
                self.remove_systems()
            if len(edsmResults) > 0:
                # there are still systems in the results -> stop here
                break
            tries += 1

        self.fire_event()


class IgnoreSystemTask(BackgroundTaskClosestSystem):
    """ Ignore a system name once, for the current EDSM session, or for a period of time. """
    def __init__(self, rse_data: RseData, system_name: str, once: bool = False, duration: int = 0):
        super(IgnoreSystemTask, self).__init__(rse_data)
        self.system_name = system_name
        self.duration = duration
        self.once = once

    def execute(self):
        for system in self.rse_data.system_list:
            if system.name.lower() == self.system_name.lower():
                # move system to back of the list. It will get sorted back to the front after a jump.
                # removing from system_list will result in it being ignored until the program's EDSM cooldown runs off
                self.rse_data.system_list.remove(system)
                self.rse_data.system_list.append(system)
                if not self.once:
                    self.rse_data.get_cached_set(RseData.CACHE_IGNORED_SYSTEMS).add(system.id64)
                    if self.duration > 0:
                        self.rse_data.add_system_to_cache(system.id64, self.duration, RseData.CACHE_IGNORED_SYSTEMS)

                self.fire_event()
                break


class VersionCheckTask(BackgroundTask):
    def __init__(self, rse_data: RseData):
        super(VersionCheckTask, self).__init__(rse_data)

    def execute(self):
        try:
            response = requests.get(RseData.VERSION_CHECK_URL, timeout=10)
            releases_info = json.loads(response.text)
            running_version = tuple(RseData.VERSION.split("."))
            for release_info in releases_info:
                if not release_info["draft"] and not release_info["prerelease"]:
                    new_version_text = release_info["tag_name"].split("_")[1]
                    new_version_info = tuple(new_version_text.split("."))
                    if running_version < new_version_info:
                        self.rse_data.last_event_info[RseData.BG_UPDATE_JSON] = {"version": new_version_text, "url": release_info["html_url"]}
                        self.rse_data.frame.event_generate(RseData.EVENT_RSE_UPDATE_AVAILABLE, when="tail")
                        break
        except Exception as e:
            logger.exception("Failed to retrieve information about available updates.")


class TimedTask(BackgroundTask):
    # the reason this class exists is to use the task queue for the timer
    def __init__(self, rse_data: RseData):
        super(TimedTask, self).__init__(rse_data)

    def execute(self):
        self.rse_data.remove_expired_systems_from_caches()


class DeleteSystemsFromCacheTask(BackgroundTask):
    def __init__(self, rse_data, cache_type: int):
        super(DeleteSystemsFromCacheTask, self).__init__(rse_data)
        self.cacheType = cache_type

    def execute(self):
        self.rse_data.remove_all_systems_from_cache(self.cacheType)


class EdsmBodyCheck(BackgroundTaskClosestSystem):
    def __init__(self, rse_data: RseData):
        super(EdsmBodyCheck, self).__init__(rse_data)

    def fire_event_edsm_body_check(self, message=None):
        self.rse_data.last_event_info[RseData.BG_EDSM_BODY] = message or "?"
        if self.rse_data.frame:
            self.rse_data.frame.event_generate(RseData.EVENT_RSE_EDSM_BODY_COUNT, when="tail")  # calls updateUI in main thread


class FSSAllBodiesFoundTask(EdsmBodyCheck):
    def __init__(self, rse_data: RseData, id64: int, edsm_body_check: bool):
        super(FSSAllBodiesFoundTask, self).__init__(rse_data)
        self.id64 = id64
        self.edsm_body_check = edsm_body_check

    def execute(self):
        system = self.get_system_from_id(self.id64)
        if system:
            system.remove_from_project(RseData.PROJECT_SCAN)
            self.remove_systems()
            self.fire_event()

        if self.edsm_body_check:
            self.rse_data.add_system_to_cache(self.id64, 2 ** 31 - 1, RseData.CACHE_FULLY_SCANNED_BODIES)  # overwrites entry in DB if it was set before
            self.fire_event_edsm_body_check("System complete")


class FSSDiscoveryScanTask(EdsmBodyCheck):
    def __init__(self, rse_data: RseData, system_name: str, body_count: int, progress: float):
        super(FSSDiscoveryScanTask, self).__init__(rse_data)
        self.system_name = system_name
        self.body_count = body_count
        self.progress = progress

    def query_edsm(self):
        edsm_url = f"https://www.edsm.net/api-system-v1/bodies?systemName={quote(self.system_name)}"
        logger.debug(f"Querying EDSM for bodies of system {self.system_name}.")
        try:
            response = requests.get(edsm_url, timeout=10)
            edsm_json = json.loads(response.text)
            return edsm_json["id64"], len(edsm_json["bodies"])
        except Exception as e:
            logger.debug("EDSM body count call failed.", exc_info=e)
        return None, None  # error/timeout occurred

    def execute(self):
        if self.progress == 1.0:
            self.fire_event_edsm_body_check("System complete")
            # no need to call EDSM's API here because all bodies are found and will be submitted to EDSM
            return

        id64, known_to_edsm = self.query_edsm()
        if id64:
            if self.body_count == known_to_edsm:
                self.rse_data.add_system_to_cache(id64, int(math.pow(2, 31)) - 1, RseData.CACHE_FULLY_SCANNED_BODIES)
            self.fire_event_edsm_body_check(f"{known_to_edsm}/{self.body_count}")
        else:
            self.fire_event_edsm_body_check(f"?/{self.body_count}")
