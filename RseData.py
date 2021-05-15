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
import tkinter

import plug
import os
import time
import math
import sqlite3
import json
import logging
from config import appname
from urllib.request import urlopen
from urllib.parse import urlencode
from typing import Dict, List, Any, Set, Union, Tuple, KeysView, Optional


logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")


class RseProject(object):
    def __init__(self, project_id: int, action_text: str, name: str, explanation: str, enabled: int):
        self.project_id = project_id
        self.action_text = action_text
        self.name = name
        self.explanation = explanation
        self.enabled = enabled


class EliteSystem(object):
    def __init__(self, id64: int, name: str, x: Union[int, float], y: Union[int, float], z: Union[int, float], uncertainty: int = 0):
        self.id64 = id64
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.uncertainty = uncertainty
        self.distance = 10000  # set initial value to be out of reach
        self.__rseProjects: Dict[int, RseProject] = dict()

    @staticmethod
    def calculate_distance(x1: Union[int, float], x2: Union[int, float], y1: Union[int, float], y2: Union[int, float], z1: Union[int, float], z2: Union[int, float]):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def get_coordinates(self) -> Tuple[Union[int, float], Union[int, float], Union[int, float]]:
        return self.x, self.y, self.z

    def update_distance_to_current_commander_position(self, x: Union[int, float], y: Union[int, float], z: Union[int, float]):
        self.distance = self.calculate_distance_to_coordinates(x, y, z)

    def calculate_distance_to_coordinates(self, x: Union[int, float], y: Union[int, float], z: Union[int, float]) -> float:
        return self.calculate_distance(self.x, x, self.y, y, self.z, z)

    def remove_from_project(self, project_id: int):
        if project_id in self.__rseProjects:
            del self.__rseProjects[project_id]

    def remove_from_all_projects(self):
        self.__rseProjects.clear()

    def add_to_project(self, rse_project: RseProject):
        self.__rseProjects.setdefault(rse_project.project_id, rse_project)

    def add_to_projects(self, rse_projects: List[RseProject]):
        for rse_project in rse_projects:
            self.add_to_project(rse_project)

    def get_project_ids(self) -> KeysView[int]:
        return self.__rseProjects.keys()

    def calculate_distance_to_system(self, system2) -> float:
        """
        Calculate distance to other EliteSystem
        :param system2: EliteSystem
        :return: distance as float
        """
        return self.calculate_distance_to_coordinates(system2.x, system2.y, system2.z)

    def get_action_text(self) -> str:
        if len(self.__rseProjects) > 0:
            return ", ".join([rse_project.action_text for rse_project in self.__rseProjects.values()])
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

    VERSION = "1.4"
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

    def __init__(self, plugin_dir: str, radius_exponent: int = DEFAULT_RADIUS_EXPONENT):
        self.plugin_dir = plugin_dir
        self.new_version_info = None
        self.system_list: List[EliteSystem] = list()  # nearby systems, sorted by distance
        self.projects_dict: Dict[int, RseProject] = dict()  # key = ID
        self.frame = None
        self.last_event_info: Dict[str, Any] = dict()  # used to pass values to UI. don't assign a new value! use clear() instead
        self.radius_exponent: int = radius_exponent
        self.frame: Union[tkinter.Frame, None] = None
        self.local_db_cursor = None
        self.local_db_connection = None
        self.ignored_projects_flags: int = 0  # bit mask of ignored projects (AND of all their IDs)

        """ 
        Dictionary of sets that contain the cached systems. 
        Key for the dictionary is the value of one of the CACHE_<type> variables. The value is the set that holds the corresponding systems 
        Key for set is the ID64 of the cached system
        """
        self.__cachedSystems: Dict[int, Set[int]] = dict()

    def get_cached_set(self, cache_type: int) -> Set[int]:
        """
        Return set of cached systems or empty set.
        :param cache_type: int
        :return set of systems as ID64:
        """
        if cache_type in self.__cachedSystems:
            return self.__cachedSystems.get(cache_type)
        else:
            return self.__cachedSystems.setdefault(cache_type, set())

    def set_frame(self, frame: tkinter.Frame):
        self.frame = frame

    def open_local_database(self):
        try:
            self.local_db_connection = sqlite3.connect(os.path.join(self.plugin_dir, "cache.sqlite"), timeout=10)
            self.local_db_cursor = self.local_db_connection.cursor()
        except Exception as e:
            error_message = "Local cache database could not be opened"
            logger.exception(error_message)
            plug.show_error(plug.show_error(f"{RseData.PLUGIN_NAME}-{RseData.VERSION}: {error_message}"))

    def close_local_database(self):
        if not self.is_local_database_accessible():
            return  # database not loaded
        self.local_db_connection.close()
        self.local_db_cursor = None
        self.local_db_connection = None

    def is_local_database_accessible(self):
        return hasattr(self, "local_db_cursor") and self.local_db_cursor

    def adjust_radius_exponent(self):
        """
        Adjust the radius to ensure that not too many systems are found (decrease network traffic and database load)
        """

        def inverse_calculate_radius(d: int) -> float:
            if d > 50:
                return math.log((d - 39) / 11, 2)
            else:
                return 0

        number_of_systems = len(self.system_list)

        # not enough systems in range
        if number_of_systems <= RseData.RADIUS_ADJUSTMENT_INCREASE:
            self.radius_exponent = int(self.radius_exponent) + 1
            if self.radius_exponent > RseData.MAX_RADIUS:
                self.radius_exponent = 10
            logger.debug(f"Found too few systems, increasing radius to {self.calculate_radius()}.")

        # too many systems in range
        elif number_of_systems >= RseData.RADIUS_ADJUSTMENT_DECREASE:
            self.radius_exponent = inverse_calculate_radius(self.system_list[RseData.RADIUS_ADJUSTMENT_DECREASE - 1].distance)
            if self.radius_exponent > RseData.MAX_RADIUS:  # prevent large radius after calculating on cached systems after switching a commander
                self.radius_exponent = 10
            logger.debug(f"Found too many systems, decreasing radius to {self.calculate_radius()}.")

        # number of systems within limits but distance exceeds set radius when using cached list -> increase radius
        elif number_of_systems > 0 and self.calculate_radius() < self.system_list[0].distance:
            self.radius_exponent = inverse_calculate_radius(self.system_list[0].distance)
            if self.radius_exponent > RseData.MAX_RADIUS:  # prevent large radius after calculating on cached systems
                self.radius_exponent = 10

    def calculate_radius(self, exponent: int = 0) -> float:
        if not exponent:
            exponent = self.radius_exponent
        return 39 + 11 * (2 ** exponent)

    def generate_ignored_actions_list(self) -> Set[int]:
        """
        TODO
        currently it ignores all systems that are part of a project. lets say we have a system that is part of 2 projects
        and the user ignores one of them. then it won't be in the list
        might want to change that and just remove the project from the local action flag
        """
        enabled_flags = set()
        combined_ignored_flags = self.ignored_projects_flags

        for rse_project in self.projects_dict.values():
            if not rse_project.enabled:
                combined_ignored_flags = combined_ignored_flags | rse_project.project_id
        for i in range(1, (2 ** len(self.projects_dict.values()))):  # generate all possible bit masks
            flag = i & ~combined_ignored_flags
            if flag > 0:
                enabled_flags.add(flag)
        return enabled_flags

    def _query_rse_api(self, rse_url: str) -> Optional[Dict]:
        """
        Internal method which only calls the API and returns a JSON object or None.
        :param rse_url:
        :return: parsed JSON or None
        """
        try:
            url = urlopen(rse_url, timeout=10)
            if url.getcode() != 200:
                # some error occurred
                logger.debug("Error calling RSE API. HTTP code: {code}.".format(code=url.getcode()))
                logger.debug("Tried to call {url}.".format(url=rse_url))
                return None
            response = url.read()
            return json.loads(response)
        except Exception as e:
            # some error occurred
            logger.debug("Error calling RSE API.", exc_info=e)
            logger.debug("Tried to call {url}.".format(url=rse_url))
            return None

    def generate_lists_from_remote_database(self, cmdr_x: Union[float, int], cmdr_y: Union[float, int], cmdr_z: Union[float, int]) -> bool:
        """
        Takes coordinates of commander and queries the server for systems that are in range. It takes the current set radius and sets any newly found
        systems to self.systemList. Returns True if new systems were found and False if no new systems were found.

        :param cmdr_x: x coordinate of current position
        :param cmdr_y: y coordinate of current position
        :param cmdr_z: z coordinate of current position
        :return: True when new systems were found and False if not
        """
        enabled_flags = self.generate_ignored_actions_list()
        if len(enabled_flags) == 0:
            return False

        if len(enabled_flags) == 2 ** len(self.projects_dict.values()) - 1:  # all projects are enabled, no need to specify any
            flags = list()
        else:
            flags = list(enabled_flags)

        params = {"x": cmdr_x, "y": cmdr_y, "z": cmdr_z,
                  "radius": self.calculate_radius(),
                  "flags": flags}
        rse_url = "https://cyberlord.de/rse/systems.py?" + urlencode(params)

        rse_json = self._query_rse_api(rse_url)  # use an extra method for unit testing purposes
        if not rse_json:
            return False

        systems: List[EliteSystem] = list()
        scanned_systems = self.get_cached_set(RseData.CACHE_FULLY_SCANNED_BODIES)

        for _row in rse_json:
            rse_id64 = _row["id"]
            rse_name = _row["name"]
            rse_x = _row["x"]
            rse_y = _row["y"]
            rse_z = _row["z"]
            uncertainty = _row["uncertainty"]
            action = _row["action_todo"]

            distance = EliteSystem.calculate_distance(cmdr_x, rse_x, cmdr_y, rse_y, cmdr_z, rse_z)
            if distance <= self.calculate_radius():
                elite_system = EliteSystem(rse_id64, rse_name, rse_x, rse_y, rse_z, uncertainty)
                elite_system.add_to_projects([rseProject for rseProject in self.projects_dict.values() if action & rseProject.project_id])
                elite_system.distance = distance

                # special case: project 4 (scan bodies)
                if RseData.PROJECT_SCAN in elite_system.get_project_ids() and elite_system.id64 in scanned_systems:
                    elite_system.remove_from_project(RseData.PROJECT_SCAN)

                if len(elite_system.get_project_ids()) > 0:
                    systems.append(elite_system)

        if len(systems) == 0:
            return False  # nothing new

        # filter out systems that have been completed or are ignored
        systems = list(filter(lambda system: system.id64 not in self.get_cached_set(RseData.CACHE_IGNORED_SYSTEMS), systems))
        systems.sort(key=lambda l: l.distance)

        self.system_list = systems
        logger.debug("Found {systems} systems within {radius} ly.".format(systems=len(systems), radius=self.calculate_radius()))

        return True

    def remove_expired_systems_from_caches(self, handle_db_connection: bool = True):
        if handle_db_connection:
            self.open_local_database()
        if not self.is_local_database_accessible():
            return  # can't do anything here

        now = time.time()
        self.local_db_cursor.execute("SELECT id64, cacheType FROM CachedSystems WHERE expirationDate <= ?", (now,))
        for row in self.local_db_cursor.fetchall():
            id64, cacheType = row
            cache = self.get_cached_set(cacheType)
            if id64 in cache:
                cache.remove(id64)
        self.local_db_cursor.execute("DELETE FROM CachedSystems WHERE expirationDate <= ?", (now,))
        self.local_db_connection.commit()

        if handle_db_connection:
            self.close_local_database()

    def remove_all_systems_from_cache(self, cache_type: int, handle_db_connection: bool = True):
        if handle_db_connection:
            self.open_local_database()
        if not self.is_local_database_accessible():
            return  # no database connection

        self.local_db_cursor.execute("DELETE FROM CachedSystems WHERE id64 NOT NULL AND cacheType = ?", (cache_type,))
        self.local_db_connection.commit()

        if handle_db_connection:
            self.close_local_database()

    def add_system_to_cache(self, id64: int, expiration_time: int, cache_type: int, handle_db_connection: bool = True):
        if handle_db_connection:
            self.open_local_database()
        if self.is_local_database_accessible():
            self.local_db_cursor.execute("INSERT OR REPLACE INTO CachedSystems VALUES (?, ?, ?)", (id64, expiration_time, cache_type))
            self.local_db_connection.commit()
        if handle_db_connection:
            self.close_local_database()

    def initialize(self):
        # initialize local cache
        self.open_local_database()
        if self.is_local_database_accessible():
            self.local_db_cursor.execute("""CREATE TABLE IF NOT EXISTS `CachedSystems` (
                                            `id64`	          INTEGER,
                                            `expirationDate`  REAL NOT NULL,
                                            `cacheType`	      INTEGER NOT NULL,
                                            PRIMARY KEY(`id64`));""")
            self.local_db_connection.commit()
            self.remove_expired_systems_from_caches(handle_db_connection=False)

            # read cached systems
            self.local_db_cursor.execute("SELECT id64, cacheType FROM CachedSystems")
            for row in self.local_db_cursor.fetchall():
                id64, cacheType = row
                self.get_cached_set(cacheType).add(id64)
            self.close_local_database()

        # initialize dictionaries
        if len(self.projects_dict) == 0:
            response = self._query_rse_api("https://cyberlord.de/rse/projects.py")
            if not response:
                errorMessage = "Could not get information about projects."
                logger.error(errorMessage)
                plug.show_error("{plugin_name}-{version}: {msg}".format(plugin_name=RseData.PLUGIN_NAME, version=RseData.VERSION, msg=errorMessage))
            else:
                for _row in response:
                    rseProject = RseProject(_row["id"], _row["action_text"], _row["project_name"], _row["explanation"], _row["enabled"])
                    self.projects_dict[rseProject.project_id] = rseProject
